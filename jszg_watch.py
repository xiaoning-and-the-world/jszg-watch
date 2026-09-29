#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
jszg_watch.py  —  浙江省教育考试院「中小学教师资格认定及定期注册」公告变更监控

监控对象
    https://www.zjzs.net/col/col62/index.html
    栏目：中小学教师资格认定及定期注册（col62，认定 + 定期注册合并为一个列表）

重点关注
    后续新增的「浙江省2026年下半年中小学教师资格认定公告」
    （当前列表里的「浙江省2026年中小学教师资格认定公告」是上半年的）。
    命中 watch.focus_keywords 的公告会被高亮并优先推送。

每次运行会
    1. 抓取 col62 列表页（公告数据内嵌在该页的 <record><![CDATA[...]]> 里）
    2. 以「文章 ID」为唯一标识与本地快照 state/snapshot.json.gz 对比，输出：
         新增 / 标题变化 / 下架 / 重新出现
    3. 只对「本次新增」的公告抓取正文（正文监控），正文摘要写进 Markdown/HTML 报告；
       历史公告的正文不再重复抓取，也不检测历史公告的正文改动
    4. 生成 Markdown + HTML 报告到 reports/（latest.md / latest.html 为最新一份）
    5. 通过 Server酱 或 PushPlus 把摘要推送到微信
    6. 配合 GitHub Actions 或 Windows 计划任务，每天自动执行

依赖：仅 Python 标准库（3.8+），不需要 pip install。

常用命令
    python jszg_watch.py             # 抓取 + 对比 + 推送（首次运行只建基线，不推送）
    python jszg_watch.py --dry-run   # 只抓取对比并打印，不推送、不写任何文件
    python jszg_watch.py --baseline  # 重建基线：把当前状态视为已知，不报警
    python jszg_watch.py --force     # 强制推送一次（用于测试推送通道）
    python jszg_watch.py --self-test # 自检：配置、网络、列表与正文解析

关于换站与误报
    · 快照里记录了 site；一旦监控目标换成别的站点，会识别为「站点已更换」并自动
      重建基线，不会把新站点的历史公告误报成新增，也不会把旧站点数据误判成下架。
    · 列表页偶尔可能少给一条（网络/缓存抖动），因此下架需要「连续 N 天都抓不到」
      才确认（watch.removal_confirm_days，默认 7 天）；某一轮抓取不完整时，
      本轮不做任何下架判定，快照按合并方式保存。
    · 确认下架的公告会从快照中移除，避免之后每天都重复告警。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import ssl
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# 路径与常量
# --------------------------------------------------------------------------

# 数据目录可以用环境变量 JSZG_DATA_DIR 覆盖：
#   · 本地跑     → 默认就是脚本所在目录
#   · GitHub 上跑 → 工作区是临时的，把 action 里的 checkout 指定成同一个目录
#                   （见 README 的 GitHub Actions 一节）即可让快照/报告落进仓库
BASE_DIR = Path(os.environ.get("JSZG_DATA_DIR") or Path(__file__).resolve().parent)
CONFIG_PATH = Path(os.environ.get("JSZG_CONFIG") or (BASE_DIR / "config.json"))
STATE_DIR = BASE_DIR / "state"
SNAPSHOT_PATH = STATE_DIR / "snapshot.json.gz"
REPORT_DIR = BASE_DIR / "reports"
# 固定名称的报告：每次覆盖、不参与 prune_reports 的按份数清理
REPORT_KEEP = ("latest.md", "latest.html", "snapshot.md", "snapshot.html", ".gitkeep")
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "run.log"
MAX_LOG_BYTES = 1_000_000    # 本地 run.log 超过约 1 MB 就截断
MAX_LOG_KEEP = 200_000       # 截断后保留尾部约 200 KB
LOG_TRUNCATED_NOTE = "……（较早的日志已截断）……\n"

IS_CI = bool(os.environ.get("CI")) or bool(os.environ.get("GITHUB_ACTIONS"))

SITE = "https://www.zjzs.net"
LIST_URL = f"{SITE}/col/col62/index.html"
COLUMN_NAME = "中小学教师资格认定及定期注册"
MONITOR_NAME = "浙江教师资格认定公告监控"   # 用于微信推送标题前缀、工作流显示名

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 jszg-watch/2.0"
)

CST = timezone(timedelta(hours=8))  # 北京时间

DEFAULT_CONFIG = {
    "enabled": True,
    "channels": {
        # 微信推送 A：Server酱 https://sct.ftqq.com  SendKey 形如 SCTxxxxx
        "serverchan": {"enabled": False, "sendkey": ""},
        # 微信推送 B：PushPlus https://www.pushplus.plus  token 为 32 位字符串
        "pushplus": {"enabled": False, "token": ""},
    },
    "notify": {
        # 无任何变化时也推送一条（默认否，避免每天打扰）
        "always_notify": False,
        # 非重点（未命中 focus_keywords）的变更是否也推送
        "notify_non_focus": True,
        # 推送消息里附带「最近公告」的条数（按发布日期倒序，0 = 不带）
        "recent_count": 5,
    },
    "watch": {
        # 监控的列表页地址（换栏目/换站只改这里，不用改代码）
        "list_url": LIST_URL,
        "column_name": COLUMN_NAME,
        # 重点关键词：标题命中任意一个即视为「重点关注」，会高亮并优先推送
        # 例如只等 2026 年下半年认定公告，可保持 ["教师资格认定"]
        "focus_keywords": ["教师资格认定"],
        # 额外关注词：命中会在报告里单独列出（例如 ["定期注册"]），留空不启用
        "extra_keywords": [],
        # 正文监控：只对「本次新增」的公告抓正文，历史公告不再重复抓、也不检测其改动
        "digest_new": True,
        # 单轮最多为多少条新增公告抓正文（防止一次性新增很多时请求过多）
        "digest_max": 10,
        # 下架要多保守：连续多少天都抓不到才确认（避免偶发少给一条）
        "removal_confirm_days": 7,
        # 请求间隔（秒），对目标站点友好
        "page_delay": 0.5,
        "timeout": 30,
        "retries": 3,
        "retry_delay": 3,
        # 该站点 TLS 证书链异常（浏览器能容错，Python 默认校验会失败）。
        # 为 true 时：默认校验失败会自动降级为「不校验证书」重试；站点修好后会自动恢复正常校验
        "allow_insecure_ssl": True,
    },
    "report": {
        "keep_last": 120,  # reports/ 只保留最近 N 份（0 = 不清理）
    },
}


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def now_cst() -> datetime:
    return datetime.now(CST)


def log(msg: str) -> None:
    line = f"[{now_cst().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    if IS_CI:
        return  # CI 里日志看 Actions 输出就够了，不往仓库写文件
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_LOG_BYTES:
            # 本地长期运行：超过上限就把旧内容截掉，只保留尾部
            tail = LOG_PATH.read_text(encoding="utf-8", errors="replace")[-MAX_LOG_KEEP:]
            LOG_PATH.write_text(LOG_TRUNCATED_NOTE + tail, encoding="utf-8")
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def strip_tags(raw: str) -> str:
    txt = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw or "")
    txt = re.sub(r"(?s)<[^>]+>", " ", txt)
    txt = html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


def deep_merge(base: dict, extra: dict) -> dict:
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config() -> dict:
    """读取 config.json。

    在 GitHub Actions 上，仓库里只有 config.example.json（不含密钥），
    真正的配置由 workflow 从仓库 Secret 生成一个临时 config.json，
    并用 JSZG_CONFIG 指向它；密钥因此不会进入仓库。
    """
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝默认值
    if CONFIG_PATH.exists():
        try:
            deep_merge(cfg, json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            log(f"警告：{CONFIG_PATH.name} 解析失败（{exc}），本次使用默认配置")
    else:
        log(f"提示：未找到 {CONFIG_PATH}，使用默认配置（未启用任何推送渠道）")

    env_map = (
        ("JSZG_SERVERCHAN_KEY", "serverchan", "sendkey"),
        ("JSZG_PUSHPLUS_TOKEN", "pushplus", "token"),
    )
    for env_name, channel, field in env_map:
        value = (os.environ.get(env_name) or "").strip()
        if value:
            cfg["channels"].setdefault(channel, {})["enabled"] = True
            cfg["channels"][channel][field] = value
            log(f"已从环境变量 {env_name} 启用 {channel} 推送")
    return cfg


def site_of(url: str) -> str:
    """从 URL 取回 scheme://netloc，用于判断「监控目标是否换了站点」。"""
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return (url or "").rstrip("/")


def _as_list(value) -> list[str]:
    """把配置项容错地转成字符串列表：字符串当单元素，非法类型忽略。

    这样即使 config.json 里把 focus_keywords 写成 "教师资格认定"（字符串而非数组），
    也不会被逐字迭代成"教/师/资/格/认/定"而把几乎每条公告都判成重点。
    """
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return []


def matches_keywords(title: str, keywords) -> bool:
    """标题命中任意一个关键词即返回 True（对字符串型误配也安全）。"""
    return any(k in (title or "") for k in _as_list(keywords))


def _int_or_none(text: str, pattern: str):
    m = re.search(pattern, text or "")
    return int(m.group(1)) if m else None


def _as_int(value, default: int) -> int:
    """容错地把配置值转成 int：缺省静默用默认值，类型错误则告警并回退。"""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        log(f"警告：配置项应为整数，实际为 {value!r}，改用默认值 {default}")
        return default


def _as_float(value, default: float) -> float:
    """容错地把配置值转成 float：缺省静默用默认值，类型错误则告警并回退。"""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        log(f"警告：配置项应为数字，实际为 {value!r}，改用默认值 {default}")
        return default


# --------------------------------------------------------------------------
# 网络层
# --------------------------------------------------------------------------

class PageMissing(Exception):
    """目标页不存在（网站对超出范围的分页直接返回 404）。"""


# 该站点证书链异常。首次校验失败后把结论记住，后续请求直接走宽松校验：
# 避免每个请求都先做一次注定失败的 TLS 握手（省一次握手 + 一次异常），
# 也避免把同一条警告日志刷屏。
_SSL_INSECURE = False
_SSL_WARNED = False


def _ssl_context(insecure: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if insecure:
        # 该站点证书链不完整（自签名证书出现在链中），Python 默认校验会失败。
        # 这里仅在 allow_insecure_ssl 为 true 且默认校验失败后才会走到。
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _is_cert_error(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ssl.SSLError):
        return True
    text = str(exc)
    return "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text


def http_get(url: str, cfg: dict) -> str:
    """带重试的 GET。证书校验失败时（且允许）自动降级为宽松校验重试。"""
    global _SSL_INSECURE, _SSL_WARNED
    watch = cfg["watch"]
    retries = max(1, _as_int(watch.get("retries"), 1))
    timeout = _as_float(watch.get("timeout"), 30)
    retry_delay = _as_float(watch.get("retry_delay"), 3)
    allow_insecure = bool(watch.get("allow_insecure_ssl", True))

    insecure = _SSL_INSECURE
    last_err: BaseException | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context(insecure)) as resp:
                raw = resp.read()
            for enc in ("utf-8", "gbk"):
                try:
                    return raw.decode(enc)
                except Exception:  # noqa: BLE001
                    continue
            return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404, 410):
                raise PageMissing(f"HTTP {exc.code} {url}") from exc
            last_err = exc
        except urllib.error.URLError as exc:
            if not insecure and allow_insecure and _is_cert_error(exc):
                if not _SSL_WARNED:
                    log("警告：TLS 证书校验失败，改用宽松校验重试（可在 watch.allow_insecure_ssl 关闭）")
                    _SSL_WARNED = True
                _SSL_INSECURE = True
                insecure = True
                continue  # 立刻重试，不占用退避次数
            last_err = exc
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        if attempt < retries:
            time.sleep(retry_delay)
    raise RuntimeError(f"请求失败 {url}: {last_err}")


def http_post(url: str, data: dict, timeout: int = 30) -> str:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------

# 列表数据内嵌在 <script type="text/xml"><datastore> 的 <record><![CDATA[...]]></record> 中
RECORD_RE = re.compile(r"<record><!\[CDATA\[(.*?)\]\]></record>", re.S | re.I)
HREF_RE = re.compile(r'href="([^"]+)"', re.I)
TITLE_RE = re.compile(r'title="([^"]*)"', re.I)
ANCHOR_TEXT_RE = re.compile(r">([^<]+)</a", re.I | re.S)
DATE_RE = re.compile(r'class="zjhz-tabcut-content-titme"\s*>([\d-]{6,12})<', re.I)
ART_ID_RE = re.compile(r"art_(\d+)_(\d+)\.html", re.I)
PROXY_RE = re.compile(r'href="(/module/web/jpage/dataproxy\.jsp\?[^"]+)"', re.I)

# 详情页：正文主体所在容器（取到二维码块或 </body> 为止）
BODY_RE = re.compile(
    r'<div class="zjhz-article-main_section">(.*?)(?:<div class="zjhz-artic_qr-code|</body>)',
    re.S | re.I,
)


def article_id(href: str) -> str:
    """以 /art/2026/9/14/art_62_12627.html 中的 art_62_12627 为唯一标识。"""
    m = ART_ID_RE.search(href or "")
    if m:
        return f"art_{m.group(1)}_{m.group(2)}"
    m = re.search(r"/([0-9]+)\.html", href or "")
    if m:
        return m.group(1)
    return hashlib.sha1((href or "").encode("utf-8")).hexdigest()[:16]


def parse_list(page_html: str, base_url: str = SITE) -> list[dict]:
    """从列表页 HTML 中解析出公告条目（标题 / 日期 / 链接）。"""
    items: list[dict] = []
    seen: set[str] = set()
    base = base_url.rstrip("/") + "/"
    for body in RECORD_RE.findall(page_html):
        m = HREF_RE.search(body)
        if not m:
            continue
        href = m.group(1).strip()
        if not href:
            continue
        aid = article_id(href)
        if aid in seen:
            continue

        t = TITLE_RE.search(body)
        title = strip_tags(t.group(1)) if t else ""
        if not title:
            t2 = ANCHOR_TEXT_RE.search(body)
            title = strip_tags(t2.group(1)) if t2 else ""
        if not title:
            continue

        d = DATE_RE.search(body)
        date = d.group(1) if d else ""
        seen.add(aid)
        items.append({
            "id": aid,
            "title": title,
            "date": date,
            "url": urllib.parse.urljoin(base, href.split("?")[0]),
            "detail_url": urllib.parse.urljoin(base, href),
        })
    return items


def parse_article(page_html: str) -> dict:
    """从详情页解析正文摘要（只保留正文主体，排除导航/浏览量/二维码）。"""
    m = BODY_RE.search(page_html)
    text = strip_tags(m.group(1) if m else "")
    return {
        "chars": len(text),
        "summary": text[:500],
    }


# --------------------------------------------------------------------------
# 抓取
# --------------------------------------------------------------------------

def fetch_list(cfg: dict) -> tuple[list[dict], bool]:
    """抓取列表页，返回 (条目列表, 是否完整)。

    col62 目前只有 8 条、全部内嵌在 index.html 中（totalRecord=8, perPage=12），
    正常情况一次请求即可。此处仍按 totalRecord/perPage 兼容未来条目增多后的翻页
    （翻页走 /module/web/jpage/dataproxy.jsp），一旦翻页失败就标记为「不完整」，
    本轮不做下架判定。
    """
    watch = cfg["watch"]
    url = watch.get("list_url") or LIST_URL
    name = watch.get("column_name") or COLUMN_NAME
    page_delay = _as_float(watch.get("page_delay"), 0.5)
    origin = site_of(url)  # 翻页基址跟随配置，而不是写死某一站点

    page_html = http_get(url, cfg)
    items = parse_list(page_html, origin)
    by_id = {it["id"]: it for it in items}

    total = _int_or_none(page_html, r"totalRecord:(\d+)")
    per_page = _int_or_none(page_html, r"perPage:(\d+)")
    complete = True
    if total is None:
        # 拿不到 totalRecord 就无法确认是否抓全；按「不完整」处理，
        # 以免站点半改版时把没抓到的公告误判成下架
        log(f"警告：{name} 未能解析 totalRecord，无法确认条目是否抓全，本轮按「不完整」处理")
        complete = False

    if total and per_page and total > len(by_id):
        pages = (total + per_page - 1) // per_page
        proxy = PROXY_RE.search(page_html)
        if not proxy:
            log(f"警告：{name} 应有 {total} 条但只解析到 {len(by_id)} 条，且未找到翻页接口")
            complete = False
        else:
            for page in range(1, pages):
                purl = origin + re.sub(r"page=\d+", f"page={page}", html.unescape(proxy.group(1)))
                try:
                    extra_html = http_get(purl, cfg)
                except Exception as exc:  # noqa: BLE001
                    log(f"警告：{name} 第 {page + 1} 页抓取失败（{exc}）")
                    complete = False
                    break
                got = parse_list(extra_html, origin)
                new_ones = [g for g in got if g["id"] not in by_id]
                if not new_ones:
                    complete = False
                    break
                for g in new_ones:
                    by_id[g["id"]] = g
                    items.append(g)
                time.sleep(page_delay)

    if total is not None and len(items) != total:
        log(f"警告：{name} 期望 {total} 条，实际解析 {len(items)} 条"
            f"{'（已标记为不完整）' if complete else ''}")
        complete = False

    log(f"{name}：抓到 {len(items)} 条（{'完整' if complete else '不完整'}）")
    return items, complete


def digest_items(items: list[dict], target_ids: set[str], cfg: dict) -> None:
    """只对本次新增（target_ids）的公告抓正文，原地写入 hash/summary 等字段。

    历史公告不重复抓取，也不检测其正文改动——这是本项目的既定取舍。
    """
    watch = cfg["watch"]
    if not watch.get("digest_new", True) or not target_ids:
        return
    limit = max(0, _as_int(watch.get("digest_max"), 0))
    targets = [it for it in items if it["id"] in target_ids][:limit] if limit else []
    if not targets:
        return

    page_delay = _as_float(watch.get("page_delay"), 0.5)
    log(f"需要抓取正文的新增公告：{len(targets)} 条")
    for idx, it in enumerate(targets, 1):
        try:
            page_html = http_get(it["detail_url"], cfg)
            it.update(parse_article(page_html))
            it["digest_tracked"] = True
        except PageMissing as exc:
            log(f"警告：正文不存在（可能刚下架）{it['title']}（{exc}）")
        except Exception as exc:  # noqa: BLE001
            log(f"警告：正文抓取失败 {it['title']}（{exc}）")
        if idx < len(targets):
            time.sleep(page_delay)


# --------------------------------------------------------------------------
# 对比
# --------------------------------------------------------------------------

def _days_between(start: str, end: str) -> int:
    try:
        a = datetime.strptime(start, "%Y-%m-%d")
        b = datetime.strptime(end, "%Y-%m-%d")
        return (b - a).days
    except Exception:  # noqa: BLE001
        return 0


def diff_snapshots(old: dict, items: list[dict], cfg: dict, complete: bool) -> dict:
    """以文章 ID 为唯一键与基线对比。

    · 新增：基线里从未见过、且不在「已确认下架」名单里的 ID；
    · 变化：标题变了（发布日期只作展示，不参与判定）；
    · 下架：连续 N 天都抓不到才确认；本轮抓取不完整时不做任何下架判定；
    · 重新出现：曾经确认下架、如今又出现了。
    """
    old_items = (old or {}).get("items") or {}
    removed_ids = set(old.get("removed_ids") or [])
    confirm_days = max(1, _as_int(cfg["watch"].get("removal_confirm_days"), 7))
    today = now_cst().strftime("%Y-%m-%d")

    new_map = {it["id"]: it for it in items}
    added, changed, removed, reappeared = [], [], [], []
    missing: dict[str, str] = {}
    reappeared_ids: set[str] = set()

    for aid, item in new_map.items():
        prev = old_items.get(aid)
        if prev is None:
            if aid in removed_ids:
                reappeared.append(item)
                reappeared_ids.add(aid)
            else:
                added.append(item)
            continue
        fields = []
        if (prev.get("title") or "") != (item.get("title") or ""):
            fields.append(f"标题：{prev.get('title')} → {item.get('title')}")
        if fields:
            changed.append({**item, "changes": fields})

    for aid, prev in old_items.items():
        if aid in new_map:
            continue
        if not complete:
            # 抓取不完整：不新起计时、不确认下架，只保留已有计时
            if prev.get("first_missing_at"):
                missing[aid] = prev["first_missing_at"]
            continue
        since = prev.get("first_missing_at") or today
        if _days_between(since, today) >= confirm_days:
            removed.append(prev)
        else:
            missing[aid] = since

    removed_set = {it["id"] for it in removed}
    new_removed_ids = (removed_ids - reappeared_ids) | removed_set
    return {
        "added": added,
        "changed": changed,
        "removed": removed,
        "reappeared": reappeared,
        "missing": missing,
        "removed_ids": sorted(new_removed_ids)[-500:],
        "confirmed_removed": removed_set,
    }


def split_by_focus(items: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    kws = _as_list(cfg["watch"].get("focus_keywords"))
    hit = [i for i in items if matches_keywords(i.get("title", ""), kws)]
    other = [i for i in items if not matches_keywords(i.get("title", ""), kws)]
    return hit, other


def recent_titles(items: list[dict], count: int) -> list[dict]:
    """按发布日期倒序取最近 count 条（日期为 YYYY-MM-DD，可直接字符串排序）。

    日期为空的条目排到最后；count <= 0 时返回空列表。
    """
    if count <= 0:
        return []
    return sorted(items, key=lambda it: it.get("date") or "", reverse=True)[:count]


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------

def build_report(checked_at: str, total: int, diff: dict, cfg: dict,
                 base_date: str | None, first_run: bool,
                 recent: list[dict] | None = None) -> dict:
    added, changed, removed = diff["added"], diff["changed"], diff["removed"]
    reappeared = diff.get("reappeared") or []
    focus_added, other_added = split_by_focus(added, cfg)
    focus_changed, other_changed = split_by_focus(changed, cfg)
    extra = [
        i for i in added + changed
        if matches_keywords(i.get("title", ""), cfg["watch"].get("extra_keywords") or [])
    ]

    has_change = bool(added or changed or removed or reappeared)
    if focus_added or focus_changed:
        level, headline = "critical", "浙江省教师资格认定公告有更新"
    elif has_change:
        level, headline = "info", "通知公告有更新"
    elif first_run:
        level, headline = "baseline", "已建立监控基线"
    else:
        level, headline = "none", "通知公告无新增、无变化"

    L: list[str] = []
    L.append("# 浙江省教育考试院 · 教师资格认定及定期注册 变更日报")
    L.append("")
    L.append(f"- 检查时间：{checked_at}")
    L.append(f"- 对比基线：{base_date or '（无，本次为首次运行）'}")
    L.append(f"- 监控栏目：[{cfg['watch'].get('column_name', COLUMN_NAME)}]({cfg['watch'].get('list_url', LIST_URL)})")
    L.append(f"- 当前公告总数：{total}")
    L.append("")

    if first_run:
        L.append("## 首次运行 · 已建立基线")
        L.append("")
        L.append("本次把当前全部公告存为基线快照，**不会**把历史公告当作新增。")
        L.append("从下一次运行开始，即可对比出「比昨天新增 / 变化」的公告。")
        L.append("")

    if focus_added:
        L.append("## ⭐ 重点关注 · 新增公告")
        L.append("")
        for it in focus_added:
            L.append(f"- **[{it['title']}]({it['url']})**")
            L.append(f"  - 发布：{it['date']}")
            if it.get("summary"):
                L.append(f"  - 摘要：{it['summary'][:200]}")
        L.append("")

    if focus_changed:
        L.append("## ⭐ 重点关注 · 内容有更新")
        L.append("")
        for it in focus_changed:
            L.append(f"- **[{it['title']}]({it['url']})**")
            L.extend(f"  - {c}" for c in it.get("changes", []))
        L.append("")

    if other_added:
        L.append(f"## 其他新增公告（{len(other_added)} 条）")
        L.append("")
        for it in other_added:
            L.append(f"- [{it['title']}]({it['url']}) — {it['date']}")
            if it.get("summary"):
                L.append(f"  - 摘要：{it['summary'][:200]}")
        L.append("")

    if other_changed:
        L.append(f"## 其他变化（{len(other_changed)} 条）")
        L.append("")
        for it in other_changed:
            L.append(f"- [{it['title']}]({it['url']}) — {it['date']}")
            L.extend(f"  - {c}" for c in it.get("changes", []))
        L.append("")

    if removed:
        L.append(f"## 已下架 / 消失（{len(removed)} 条）")
        L.append("")
        for it in removed:
            L.append(f"- {it['title']} — {it.get('date', '')}")
        L.append("")

    if reappeared:
        L.append(f"## 之前已下架 · 现已重新出现（{len(reappeared)} 条）")
        L.append("")
        for it in reappeared:
            L.append(f"- [{it['title']}]({it['url']}) — {it['date']}")
        L.append("")

    if extra:
        L.append("## 关注关键词命中")
        L.append("")
        for it in extra:
            L.append(f"- {it['title']} — {it['date']}")
        L.append("")

    if not has_change and not first_run:
        L.append("## 结论")
        L.append("")
        L.append("与基线相比，通知公告**没有新增、没有修改、没有下架**。")
        L.append("")

    L.append("---")
    L.append(f"<sub>由 jszg_watch.py 自动生成 · 数据来源 [浙江省教育考试院]({cfg['watch'].get('list_url', LIST_URL)})</sub>")

    # 微信推送内容：按 Markdown 组织（Server酱 的 desp、PushPlus 的 template=markdown
    # 都是 Markdown 渲染）。要点：① 区块前后留空行；② 列表项用 "- "。
    # 否则 Markdown 会把**单个换行折叠成空格**，整条消息挤成"一片"。
    S = [f"**{headline}**", "", f"检查时间：{checked_at}"]

    def block(title: str, lines: list[str]) -> None:
        if lines:
            S.extend(["", f"**{title}**", "- " + "\n- ".join(lines)])

    block("⭐ 重点·新增", [f"{i['title']}（{i['date']}）" for i in focus_added[:6]])
    block("⭐ 重点·更新", [i["title"] for i in focus_changed[:6]])
    block(f"其他新增 {len(other_added)} 条", [i["title"] for i in other_added[:6]])
    block(f"其他变化 {len(other_changed)} 条", [i["title"] for i in other_changed[:6]])
    block(f"下架 {len(removed)} 条", [i["title"] for i in removed[:6]])
    block(f"下架后重新出现 {len(reappeared)} 条", [i["title"] for i in reappeared[:6]])
    block(f"最近公告 {len(recent)} 条", [f"{i.get('date', '')} {i['title']}" for i in recent])

    if first_run:
        S += ["", "本次为首次运行，已建立基线，明日起开始对比。"]
    elif not has_change:
        S += ["", "与基线一致，无需处理。"]
    if not IS_CI:
        S += ["", f"完整报告：{REPORT_DIR / 'latest.html'}"]

    markdown = "\n".join(L)
    return {
        "level": level,
        "headline": headline,
        "has_change": has_change,
        "first_run": first_run,
        "markdown": markdown,
        "html": markdown_to_html(markdown),
        "summary": "\n".join(S),
        "counts": {
            "added": len(added),
            "changed": len(changed),
            "removed": len(removed),
            "reappeared": len(reappeared),
            "focus_added": len(focus_added),
            "focus_changed": len(focus_changed),
        },
    }


def markdown_to_html(md: str) -> str:
    out: list[str] = []
    for line in md.splitlines():
        if line.startswith("### "):
            out.append(f"<h3>{inline_md(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline_md(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{inline_md(line[2:])}</h1>")
        elif line.strip() == "---":
            out.append("<hr>")
        elif line.startswith("  - "):
            out.append(f"<div class='sub'>{inline_md(line[4:])}</div>")
        elif line.startswith("- "):
            out.append(f"<li>{inline_md(line[2:])}</li>")
        elif line.strip() == "":
            out.append("")
        else:
            out.append(f"<p>{inline_md(line)}</p>")
    body = "\n".join(out)
    body = re.sub(r"(?:<li>.*?</li>\n?)+", lambda m: f"<ul>{m.group(0)}</ul>", body, flags=re.S)
    css = (
        "body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;max-width:880px;"
        "margin:24px auto;padding:0 16px;line-height:1.75;color:#1f2430}"
        "h1{font-size:22px;border-bottom:2px solid #2350A1;padding-bottom:8px}"
        "h2{font-size:17px;color:#2350A1;margin-top:24px}"
        "a{color:#2350A1}.sub{margin-left:20px;color:#5b6475;font-size:13px}"
        "hr{border:none;border-top:1px solid #e3e6ee;margin:24px 0}li{margin:4px 0}"
    )
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>教师资格认定公告日报</title><style>{css}</style></head>"
        f"<body>{body}</body></html>"
    )


def inline_md(text: str) -> str:
    text = html.escape(text.strip())
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r"<a href='\2'>\1</a>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text


# --------------------------------------------------------------------------
# 微信推送
# --------------------------------------------------------------------------

def _push_once(url: str, data: dict, is_ok) -> tuple[bool, str]:
    """POST 一次并按 is_ok 判断是否成功；失败自动重试一次（避免偶发 5xx 丢提醒）。"""
    last = ""
    for attempt in (1, 2):
        try:
            txt = http_post(url, data)
            if is_ok(txt):
                return True, txt
            last = txt
        except Exception as exc:  # noqa: BLE001
            last = f"异常：{exc}"
        if attempt == 1:
            time.sleep(2)
    return False, last


def _push_multi(values, send_one, field: str) -> tuple[bool, str]:
    """对一组账号逐个推送，返回 (是否全部成功, 说明文字)。

    values 支持「单个字符串」或「字符串数组」——想推多个账号就写成数组：
        "sendkey": ["SCT账号A", "SCT账号B"]
    """
    accounts = _as_list(values)
    if not accounts:
        return False, f"未配置 {field}"
    ok_count = 0
    detail: list[str] = []
    for idx, value in enumerate(accounts, 1):
        ok, txt = send_one(value)
        ok_count += 1 if ok else 0
        if len(accounts) == 1:  # 单账号：保持原来的简洁输出
            return ok, (txt[:300] if ok else f"推送失败：{txt[:200]}")
        detail.append(f"#{idx}{'成功' if ok else '失败'}")
        if not ok:
            detail[-1] += f"({txt[:80].strip()})"
    return ok_count == len(accounts), f"{ok_count}/{len(accounts)} 成功｜{' '.join(detail)}"


def push_serverchan(cfg: dict, title: str, body: str) -> tuple[bool, str]:
    def send_one(key: str):
        host = "https://sc.ftqq.com" if key.upper().startswith("SCU") else "https://sctapi.ftqq.com"
        return _push_once(
            f"{host}/{key}.send", {"title": title, "desp": body},
            lambda t: '"code":0' in t.replace(" ", "") or '"errno":0' in t.replace(" ", ""),
        )

    return _push_multi(cfg["channels"]["serverchan"].get("sendkey"), send_one, "sendkey")


def push_pushplus(cfg: dict, title: str, body: str) -> tuple[bool, str]:
    def send_one(token: str):
        return _push_once(
            "https://www.pushplus.plus/send",
            {"token": token, "title": title, "content": body, "template": "markdown"},
            lambda t: '"code":200' in t.replace(" ", ""),
        )

    return _push_multi(cfg["channels"]["pushplus"].get("token"), send_one, "token")


# 渠道注册表：新增渠道只需在这里加一行
PROVIDERS = [
    ("Server酱", "serverchan", push_serverchan),
    ("PushPlus", "pushplus", push_pushplus),
]


def notify(cfg: dict, title: str, body: str,
           dry_run: bool = False) -> tuple[list[str], bool]:
    """逐个渠道推送，返回 (每个渠道的结果, 是否存在「最终失败」的渠道)。

    · 每个账号失败已在 _push_once 内部重试过一次；
    · 同渠道配了多个账号时，只要有一个账号最终失败就算失败；
    · 没有任何渠道被启用 / dry-run 时不算失败。
    """
    results: list[str] = []
    had_failure = False
    for label, key, fn in PROVIDERS:
        conf = cfg["channels"].get(key, {})
        if not conf.get("enabled"):
            continue
        if dry_run:
            results.append(f"[dry-run] 跳过 {label} 推送")
            continue
        ok, msg = fn(cfg, title, body)
        if not ok:
            had_failure = True
        line = f"{label}: {'成功' if ok else '失败'} {msg}"
        results.append(line)
        log(line)

    if not results:
        results.append("未启用任何推送渠道（可在 config.json 里开启 Server酱 / PushPlus）")
        log(results[-1])
    return results, had_failure


def windows_toast(title: str, message: str) -> None:
    """可选的 Windows 桌面气泡提醒；失败不影响主流程。

    标题与内容通过环境变量传给 PowerShell，避免拼接命令带来的注入风险。
    只在本地 Windows 上弹，CI（GitHub Actions）里直接跳过。
    """
    if IS_CI or os.name != "nt":
        return
    try:
        ps = (
            "$t=$env:JSZG_TOAST_TITLE;$m=$env:JSZG_TOAST_MSG;"
            "[reflection.assembly]::loadwithpartialname('System.Windows.Forms')|Out-Null;"
            "[reflection.assembly]::loadwithpartialname('System.Drawing')|Out-Null;"
            "$n=New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
            "$n.ShowBalloonTip(10000,$t,$m,0);"
            "Start-Sleep -Seconds 8;$n.Dispose()"
        )
        env = dict(os.environ, JSZG_TOAST_TITLE=title, JSZG_TOAST_MSG=message)
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            timeout=30,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


# --------------------------------------------------------------------------
# 快照读写
# --------------------------------------------------------------------------

def _read_json_gz(path: Path):
    """读取 gzip 压缩的 JSON 快照（用 gzip 存，每天提交到仓库时体积小得多）。"""
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))


def _write_json_gz(path: Path, obj) -> None:
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(payload, 9, mtime=0))  # mtime=0 → 内容相同则字节相同
    tmp.replace(path)


def load_snapshot() -> dict:
    if SNAPSHOT_PATH.exists():
        try:
            return _read_json_gz(SNAPSHOT_PATH)
        except Exception as exc:  # noqa: BLE001
            log(f"警告：{SNAPSHOT_PATH.name} 读取失败（{exc}），按首次运行处理")
    return {}


def write_snapshot(merged: dict, checked_at: str, missing: dict, removed_ids: list,
                   note: str = "", runs: int = 1,
                   site: str | None = None, list_url: str | None = None) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 4,
        "site": site or site_of(LIST_URL),
        "list_url": list_url or LIST_URL,
        "checked_at": checked_at,
        "total": len(merged),
        "note": note,
        "run_count": runs,
        "missing": missing or {},
        "removed_ids": removed_ids or [],
        "items": merged,
    }
    _write_json_gz(SNAPSHOT_PATH, data)


def save_snapshot(old: dict, items: list[dict], diff: dict, checked_at: str,
                  note: str = "", site: str | None = None,
                  list_url: str | None = None) -> None:
    """保存快照。

    · items 是本轮抓到的公告；
    · 本轮没抓到、但基线里还有、且尚未确认下架的公告会「合并回」快照
      （只增不减），避免一次网络失败就把历史公告误判成新增；
    · 已确认下架的公告从快照中移除，避免之后每天重复告警。
    """
    merged = {it["id"]: it for it in items}
    old_items = (old or {}).get("items") or {}
    missing = dict((diff or {}).get("missing") or {})
    confirmed_removed = set((diff or {}).get("confirmed_removed") or [])

    for aid, prev in old_items.items():
        if aid in merged or aid in confirmed_removed:
            continue
        keep = dict(prev)
        if aid in missing:
            keep["first_missing_at"] = missing[aid]
        merged[aid] = keep

    runs = _as_int((old or {}).get("run_count"), 0) + 1
    write_snapshot(
        merged, checked_at, missing,
        (diff or {}).get("removed_ids") or [],
        note, runs, site=site, list_url=list_url,
    )
    log(f"快照已更新：{SNAPSHOT_PATH}（{len(merged)} 条"
        + (f"，其中 {len(missing)} 条待确认下架" if missing else "") + "）")


def save_reports(report: dict, stamp: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    html_path = REPORT_DIR / f"{stamp}.html"
    (REPORT_DIR / f"{stamp}.md").write_text(report["markdown"], encoding="utf-8")
    html_path.write_text(report["html"], encoding="utf-8")
    (REPORT_DIR / "latest.md").write_text(report["markdown"], encoding="utf-8")
    (REPORT_DIR / "latest.html").write_text(report["html"], encoding="utf-8")
    return html_path


def build_snapshot_listing(checked_at: str, items: list[dict], cfg: dict) -> str:
    """生成「通知公告快照」：本轮抓到的**全部**公告标题，按发布日期倒序完整列出。

    顶部给一行汇总（总数 / 日期范围 / 年份分布），下面是完整标题列表。
    """
    ordered = sorted(items, key=lambda it: it.get("date") or "", reverse=True)

    years: dict[str, int] = {}
    for it in ordered:
        year = (it.get("date") or "")[:4] or "未知"
        years[year] = years.get(year, 0) + 1
    year_line = " / ".join(f"{y} 年 {n} 条" for y, n in sorted(years.items(), reverse=True))

    dates = [it.get("date") for it in ordered if it.get("date")]
    range_line = f"{dates[-1]} ~ {dates[0]}" if dates else "（无日期）"
    column = cfg["watch"].get("column_name") or COLUMN_NAME
    list_url = cfg["watch"].get("list_url", LIST_URL)

    L: list[str] = []
    L.append(f"# 通知公告快照 · {column}")
    L.append("")
    L.append(f"- 生成时间：{checked_at}")
    L.append(f"- 数据来源：{list_url}")
    L.append(f"- 公告总数：{len(ordered)}")
    L.append(f"- 日期范围：{range_line}")
    L.append(f"- 年份分布：{year_line or '（无）'}")
    L.append("")
    L.append(f"## 完整标题列表（{len(ordered)} 条，按发布日期倒序）")
    L.append("")
    for idx, it in enumerate(ordered, 1):
        L.append(f"{idx}. {it.get('date') or '无日期'} · [{it.get('title', '')}]({it.get('url', '')})")
    L.append("")
    L.append("---")
    L.append(f"<sub>由 jszg_watch.py 自动生成 · 数据来源 [浙江省教育考试院]({list_url})</sub>")
    return "\n".join(L)


def save_snapshot_listing(markdown: str) -> Path:
    """把「通知公告快照」写进 reports/snapshot.md 与 reports/snapshot.html（每次覆盖）。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "snapshot.md").write_text(markdown, encoding="utf-8")
    html_path = REPORT_DIR / "snapshot.html"
    html_path.write_text(markdown_to_html(markdown), encoding="utf-8")
    return html_path


def prune_reports(keep_last: int) -> None:
    if not keep_last or keep_last <= 0 or not REPORT_DIR.exists():
        return
    files = sorted(
        (p for p in REPORT_DIR.glob("*") if p.name not in REPORT_KEEP),
        key=lambda p: p.name,   # 文件名即 YYYYMMDD-HHMMSS；Git 不保留 mtime，按名字排才可靠
        reverse=True,
    )
    for old in files[keep_last:]:
        try:
            old.unlink()
        except Exception:
            pass


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def run(args) -> int:
    cfg = load_config()
    if not cfg.get("enabled", True) and not args.dry_run:
        log("配置 enabled=false，跳过本次运行")
        return 0

    checked_at = now_cst().strftime("%Y-%m-%d %H:%M:%S")
    list_url = cfg["watch"].get("list_url") or LIST_URL
    cur_site = site_of(list_url)
    log("=" * 62)
    log(f"开始检查 {list_url}")

    old = load_snapshot()
    old_items = old.get("items") or {}
    site_changed = bool(old_items) and old.get("site") != cur_site
    first_run = (not old_items) or site_changed
    base_date = old.get("checked_at")

    if site_changed:
        log("注意：检测到监控目标已更换（快照 site 与当前站点不一致），"
            "将重建基线；旧站点数据不会当作新增或下架。")
    elif first_run and not args.baseline:
        log("注意：没有找到快照。在 GitHub Actions 上通常说明 state/ 没被拉取到，")
        log("      或仓库里还没有提交过 state/snapshot.json.gz。本次将建立新基线、不推送。")

    work_old = {} if (first_run or args.baseline) else old

    try:
        items, complete = fetch_list(cfg)
    except Exception as exc:  # noqa: BLE001
        log(f"抓取失败：{exc}")
        if args.dry_run:
            traceback.print_exc()
        notify(
            cfg,
            f"{MONITOR_NAME}｜抓取失败",
            f"抓取 {list_url} 失败：{exc}\n请检查网络或稍后重试。",
            dry_run=args.dry_run,
        )
        return 2

    if not items:
        log("没有解析到任何公告，页面结构可能已变化")
        return 3
    log(f"本次共解析到 {len(items)} 条公告{'（完整）' if complete else '（不完整）'}")

    # 推送消息里附带的「最近公告」（按发布日期倒序）
    recent = recent_titles(items, _as_int(cfg["notify"].get("recent_count"), 5))

    # 只对「本次新增」的公告抓正文（正文监控）；建基线时不需要抓
    work_items = work_old.get("items") or {}
    if not first_run and not args.baseline:
        new_ids = {it["id"] for it in items if it["id"] not in work_items}
        digest_items(items, new_ids, cfg)

    if first_run or args.baseline:
        diff = {
            "added": [], "changed": [], "removed": [], "reappeared": [],
            "missing": {}, "removed_ids": [], "confirmed_removed": set(),
        }
        report = build_report(checked_at, len(items), diff, cfg, None, first_run=True,
                              recent=recent)
        log("已建立基线快照（本次不产生变更告警）")
        if not args.dry_run:
            # 用 work_old：站点更换/重建基线时不能把旧站点条目合并进来
            save_snapshot(work_old, items, diff, checked_at, note="baseline",
                          site=cur_site, list_url=list_url)
    else:
        diff = diff_snapshots(work_old, items, cfg, complete)
        for aid in diff["missing"]:
            prev = work_items.get(aid, {})
            log(f"  {prev.get('title', aid)}：最近未在列表中出现（自 {diff['missing'][aid]} 起），暂不告警")
        report = build_report(checked_at, len(items), diff, cfg, base_date, first_run=False,
                              recent=recent)
        log(
            f"对比结果：新增 {len(diff['added'])}，变化 {len(diff['changed'])}，"
            f"下架 {len(diff['removed'])}，重新出现 {len(diff['reappeared'])}"
        )
        if not args.dry_run:
            save_snapshot(work_old, items, diff, checked_at,
                          site=cur_site, list_url=list_url)

    stamp = now_cst().strftime("%Y%m%d-%H%M%S")
    if not args.dry_run:
        path = save_reports(report, stamp)
        log(f"报告已保存：{path}")
        # 通知公告快照：本轮抓到的全部标题（每次覆盖 reports/snapshot.md / .html）
        snap_path = save_snapshot_listing(build_snapshot_listing(checked_at, items, cfg))
        log(f"通知公告快照已保存：{snap_path}")
        prune_reports(_as_int(cfg["report"].get("keep_last"), 0))

    print("-" * 62)
    print(report["markdown"])
    print("-" * 62)

    counts = report["counts"]
    focus_hit = bool(counts["focus_added"] or counts["focus_changed"])
    non_focus_hit = bool(report["has_change"]) and not focus_hit
    should_push = args.force or cfg["notify"].get("always_notify", False) or focus_hit or (
        non_focus_hit and cfg["notify"].get("notify_non_focus", True)
    )
    if first_run and not args.force:
        log("首次运行只建立基线，不推送；如需测试推送请加 --force")
        should_push = False

    rc = 0
    if should_push:
        _, failed = notify(
            cfg, f"{MONITOR_NAME}｜{report['headline']}", report["summary"],
            dry_run=args.dry_run,
        )
        if failed:
            log("警告：推送渠道重试后仍失败，请检查配置或网络")
            rc = 4          # 让 Actions 变红，避免「提醒静默丢失」
        if focus_hit and not args.dry_run:
            windows_toast("教师资格认定公告有更新", "发现重点公告变更，请查看微信推送")
    else:
        log("无变化或未命中推送条件，不发送通知")

    log("检查完成")
    return rc


def self_test() -> int:
    cfg = load_config()
    print("== 自检 ==")
    print(f"程序目录   : {BASE_DIR}")
    print(f"配置文件   : {CONFIG_PATH}  [{'存在' if CONFIG_PATH.exists() else '缺失，将用默认配置'}]")
    print(f"快照文件   : {SNAPSHOT_PATH}  [{'存在' if SNAPSHOT_PATH.exists() else '尚未创建'}]")
    print(f"监控栏目   : {cfg['watch'].get('column_name', COLUMN_NAME)}")
    print(f"列表地址   : {cfg['watch'].get('list_url', LIST_URL)}")
    for name, conf in cfg["channels"].items():
        state = "已启用" if conf.get("enabled") else "未启用"
        print(f"推送渠道   : {name} — {state}")

    print("\n-- 列表抓取 --")
    try:
        items, complete = fetch_list(cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"列表抓取失败：{exc}")
        return 1
    if not items:
        print("没有解析到任何公告，页面结构可能已变化")
        return 1

    focus_kws = _as_list(cfg["watch"].get("focus_keywords"))
    focus = [i for i in items if matches_keywords(i["title"], focus_kws)]
    print(f"  共 {len(items)} 条，其中重点（{'/'.join(focus_kws)}）{len(focus)} 条")
    for i in items[:12]:
        print(f"      · {i['date']}  {i['title']}")

    print("\n-- 正文解析 --")
    try:
        page_html = http_get(items[0]["detail_url"], cfg)
        info = parse_article(page_html)
        print(f"  最新一条：{items[0]['title']}")
        print(f"  正文长度：{info['chars']} 字")
        if not info["chars"]:
            print("  警告：正文为空，正文容器可能已变化")
            return 1
    except Exception as exc:  # noqa: BLE001
        print(f"  正文抓取失败：{exc}")
        return 1

    print("\n-- 快照与报告（离线） --")
    snap = load_snapshot()
    if snap.get("items"):
        print(f"  快照可读：{len(snap['items'])} 条"
              f"（基线时间 {snap.get('checked_at')}，站点 {snap.get('site')}）")
    else:
        print("  提示：尚无快照，首次运行会建立基线")
    try:
        demo = build_report(
            now_cst().strftime("%Y-%m-%d %H:%M:%S"), len(items),
            {"added": [], "changed": [], "removed": [], "reappeared": [],
             "missing": {}, "removed_ids": [], "confirmed_removed": set()},
            cfg, snap.get("checked_at"), first_run=False,
            recent=recent_titles(items, _as_int(cfg["notify"].get("recent_count"), 5)),
        )
        print(f"  报告生成正常（Markdown {len(demo['markdown'])} 字符）")
        listing = build_snapshot_listing(now_cst().strftime("%Y-%m-%d %H:%M:%S"), items, cfg)
        print(f"  通知公告快照生成正常（{len(listing)} 字符，{len(items)} 条标题）")
    except Exception as exc:  # noqa: BLE001
        print(f"  报告生成失败：{exc}")
        return 1

    print("\n自检通过。" if complete else "\n自检完成，但列表抓取不完整，请检查网络。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="浙江省教育考试院 教师资格认定及定期注册 公告变更监控"
    )
    parser.add_argument("--dry-run", action="store_true", help="只检查并打印，不推送、不写文件")
    parser.add_argument("--baseline", action="store_true", help="把当前状态设为基线，不产生告警")
    parser.add_argument("--force", action="store_true", help="强制推送一次（测试推送通道用）")
    parser.add_argument("--self-test", action="store_true", help="自检：配置、网络、列表与正文解析")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001
        log(f"运行异常：{exc}")
        log(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
