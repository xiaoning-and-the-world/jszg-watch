#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
jszg_watch.py  —  中国教师资格网「资格资讯 > 通知公告」每日变更监控

监控对象
    https://www.jszg.edu.cn/news.html
      ├─ 教师资格认定      (栏目 id=107)
      └─ 教师资格定期注册  (栏目 id=181)

每次运行会
    1. 抓取上述两个栏目的全部分页公告（标题 / 发布日期 / 链接）
    2. 对最近若干页的公告抓正文指纹，用于发现「公告内容被修改」
    3. 与本地快照 state/snapshot.json 对比，输出：
         新增 / 内容变化 / 下架
       并单独高亮「浙江省」相关的教师资格认定公告
    4. 生成 Markdown + HTML 报告到 reports/（latest.md / latest.html 为最新一份）
    5. 通过 Server酱 或 PushPlus 把摘要推送到微信
    6. 配合 Windows 计划任务，每天 09:00 自动执行

依赖：仅 Python 标准库（3.8+），不需要 pip install。

常用命令
    python jszg_watch.py             # 抓取 + 对比 + 推送（首次运行只建基线，不推送）
    python jszg_watch.py --dry-run   # 只抓取对比并打印，不推送、不写文件
    python jszg_watch.py --baseline  # 重建基线：把当前状态视为已知，不报警
    python jszg_watch.py --force     # 强制推送一次（用于测试推送通道）
    python jszg_watch.py --self-test # 自检：配置、网络、栏目是否可抓

关于误报（重要，请读一下）
    该站点的列表页与详情页前面有一层多版本缓存：同一次连续请求，可能分别命中
    「今天」「3 天前」「5 天前」等不同旧版本，导致发布日期、正文、甚至某个公告
    会不会出现在列表里都会来回抖动。为此本脚本：
      · 以「文章 ID」而不是位置或日期作为唯一标识来判定新增/下架；
      · 列表发布日期只作展示，不参与变更判定；
      · 正文改动：连续两次取样都偏离历史已知版本才算「疑似改动」，并且还需要
        在「下一次运行」时再次出现同样的新版本才确认，避免缓存抖动误报；
      · 下架：需要连续 N 次（默认 2 次）抓取都找不到才确认，避免漏抓误报；
      · 某一轮抓取不完整时，不判定下架，快照按合并方式保存。
    因此，内容改动与下架的告警会延迟一次运行（对「每日检查」来说最多晚一天），
    换来的是几乎为零的误报。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
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
PARTIAL_PATH = STATE_DIR / "partial.json.gz"
REPORT_DIR = BASE_DIR / "reports"
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "run.log"

IS_CI = bool(os.environ.get("CI")) or bool(os.environ.get("GITHUB_ACTIONS"))

SITE = "https://www.jszg.edu.cn"
NEWS_PAGE = f"{SITE}/news.html"

# 栏目：对应 news.html 里 newsData.data.list_title 的 id
SECTIONS = [
    {"id": "107", "name": "教师资格认定"},
    {"id": "181", "name": "教师资格定期注册"},
]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 jszg-watch/1.0"
)

CST = timezone(timedelta(hours=8))  # 北京时间

MAX_KNOWN_HASHES = 8  # 每篇公告保留多少个「已知内容版本」指纹

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
        # 非浙江省的变更是否也推送（默认是；设为 false 则只在浙江有变动时打扰你）
        "notify_non_zhejiang": True,
    },
    "watch": {
        # 标题含这些关键词即视为「浙江省相关」
        "province_keywords": ["浙江"],
        # 额外关注词：命中会在报告里单独列出（例如 ["定期注册"]），留空不启用
        "extra_keywords": [],
        # 抓取分页上限（1 页 10 条），防止网站异常时无限翻页
        "max_pages": 80,
        # 列表抓取遍数：站点有多版本缓存，多抓几遍按 ID 合并更不容易漏公告
        "list_passes": 2,
        # 对每个栏目最近 N 页的公告抓正文指纹，用来发现「内容被改动」
        "digest_pages": 2,
        # 下架要多保守：连续多少天都抓不到才确认（站点缓存会让公告临时消失）
        "removal_confirm_days": 7,
        # 请求间隔（秒），对目标站点友好
        "page_delay": 0.3,
        "timeout": 30,
        "retries": 3,
        "retry_delay": 3,
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
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def strip_tags(raw: str) -> str:
    txt = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
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


def matches_keywords(title: str, keywords: list[str]) -> bool:
    return any(k and k in (title or "") for k in keywords)


# --------------------------------------------------------------------------
# 抓取
# --------------------------------------------------------------------------

class PageMissing(Exception):
    """目标页不存在（网站对超出范围的分页直接返回 404）。"""


def http_get(url: str, cfg: dict) -> str:
    last_err = None
    for attempt in range(1, int(cfg["watch"]["retries"]) + 1):
        try:
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
            with urllib.request.urlopen(req, timeout=cfg["watch"]["timeout"]) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404, 410):
                raise PageMissing(f"HTTP {exc.code} {url}") from exc
            last_err = exc
            if attempt < int(cfg["watch"]["retries"]):
                time.sleep(float(cfg["watch"]["retry_delay"]))
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < int(cfg["watch"]["retries"]):
                time.sleep(float(cfg["watch"]["retry_delay"]))
    raise RuntimeError(f"请求失败 {url}: {last_err}")


ITEM_RE = re.compile(
    r'<a\s+href="(?P<href>[^"]+)"[^>]*>(?P<title>[^<]+)</a>\s*'
    r'<span\s+class="fr">(?P<date>[\d-]+)</span>',
    re.I,
)


def article_id(href: str) -> str:
    m = re.search(r"/(a\d+)\.html", href)
    return m.group(1) if m else hashlib.sha1(href.encode("utf-8")).hexdigest()[:16]


def fetch_section(section: dict, cfg: dict) -> tuple[list[dict], bool]:
    """抓取一个栏目的所有分页，返回 (条目列表, 是否完整)。

    站点列表页有多版本缓存，同一页多请求几次可能拿到「今天 / 上周」等不同快照，
    偶尔还会整条公告缺失。因此这里默认抓 `list_passes` 遍并把结果按文章 ID 合并，
    尽量把「最新版本」里的公告捞全。
    """
    passes = max(1, int(cfg["watch"].get("list_passes") or 1))
    max_pages = int(cfg["watch"]["max_pages"])
    page_delay = float(cfg["watch"]["page_delay"])

    merged: dict[str, dict] = {}
    order: list[str] = []
    last_content_page = max_pages
    complete = True

    for p in range(1, passes + 1):
        bound = min(max_pages, last_content_page + 1)
        hit_end = False
        for page in range(1, bound + 1):
            url = f"{SITE}/pages/html/{section['id']}/{page}.html"
            try:
                raw = http_get(url, cfg)
            except PageMissing:
                last_content_page = min(last_content_page, page - 1)
                hit_end = True
                break
            except Exception as exc:  # noqa: BLE001
                if page == 1:
                    raise
                log(f"警告：{section['name']} 第 {p} 遍第 {page} 页抓取失败，停止本遍（{exc}）")
                complete = False
                break

            found = ITEM_RE.findall(raw)
            if not found:
                last_content_page = min(last_content_page, page - 1)
                hit_end = True
                break

            for href, title, date in found:
                aid = article_id(href)
                title = strip_tags(title)
                if aid not in merged:
                    order.append(aid)
                    merged[aid] = {
                        "id": aid,
                        "section": section["name"],
                        "section_id": section["id"],
                        "page": page,
                        "title": title,
                        "date": date,
                        "url": urllib.parse.urljoin(SITE + "/", href.split("?")[0]),
                        "detail_url": urllib.parse.urljoin(SITE + "/", href),
                        "passes_seen": 1,
                    }
                else:
                    entry = merged[aid]
                    entry["passes_seen"] += 1
                    # 不同缓存版本给出的发布日期可能不同，取最新看到的那个
                    if date > entry.get("date", ""):
                        entry["date"] = date
                    if title != entry.get("title"):
                        entry["title"] = title

            if page < bound:
                time.sleep(page_delay)
        if not hit_end and complete:
            log(f"警告：{section['name']} 第 {p} 遍到达抓取页数上限 {max_pages}，结果可能不全")
            complete = False

    items = [merged[aid] for aid in order]
    log(
        f"{section['name']}：抓到 {len(items)} 条"
        f"（{'完整' if complete else '不完整'}，{passes} 遍合并）"
    )
    return items, complete


def fetch_article_digest(url: str, cfg: dict) -> dict:
    """抓取公告正文，返回内容指纹与摘要。"""
    try:
        raw = http_get(url.split("?")[0], cfg)
    except Exception as exc:  # noqa: BLE001
        log(f"警告：正文抓取失败 {url}（{exc}）")
        return {"hash": "", "chars": 0, "summary": "", "publish": "", "source": ""}

    text = strip_tags(raw)
    publish = ""
    m = re.search(r"发布时间：\s*(\d{4}-\d{2}-\d{2})", text)
    if m:
        publish = m.group(1)
    source = ""
    m = re.search(r"来源：\s*([^\s]{1,20})", text)
    if m:
        source = m.group(1)

    # 去掉导航等固定前缀，只保留正文主体
    for marker in ("发布时间：", "来源：", "A+"):
        idx = text.find(marker)
        if idx > 0:
            text = text[idx:]
            break

    return {
        "hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "chars": len(text),
        "summary": text[:500],
        "publish": publish,
        "source": source,
    }


def known_hashes(prev: dict) -> set[str]:
    """兼容旧版快照里单个 hash 字段的写法。"""
    got = set(prev.get("hashes") or [])
    if prev.get("hash"):
        got.add(prev["hash"])
    return {h for h in got if h}


def detect_body_change(it: dict, prev: dict, cfg: dict) -> tuple[dict, bool, str]:
    """检测公告正文是否被改动，返回 (正文信息, 是否确认改动, 待确认的新版本指纹)。

    流程（用于对抗站点多版本缓存造成的抖动）：
      1. 取样一次。命中的版本在历史已知集合里 → 没改动。
      2. 未知版本 → 立刻再取样一次：
           · 第二次落回已知版本   → 缓存抖动，忽略；
           · 两次都是同一个新版本 → 与上一次运行记录的「待确认版本」比对，
                                   一致才确认改动（即需要跨两次运行稳定出现）。
    """
    known = known_hashes(prev)
    pending = prev.get("pending_hash", "")

    first = fetch_article_digest(it["detail_url"], cfg)
    if not first["hash"] or not known:
        return first, False, ""

    if first["hash"] in known:
        return first, False, ""

    time.sleep(0.2)
    second = fetch_article_digest(it["detail_url"], cfg)
    if not second["hash"]:
        return first, False, ""
    if second["hash"] in known:
        return second, False, ""          # 第二次落回历史版本 → 抖动
    if second["hash"] != first["hash"]:
        return second, False, second["hash"]  # 仍在抖动，本版本留作待确认
    return second, (pending == second["hash"]), second["hash"]


def collect(cfg: dict, old: dict) -> tuple[list[dict], bool]:
    """抓取全部公告，并对最近若干页的公告做正文改动检测。"""
    items: list[dict] = []
    complete = True
    for section in SECTIONS:
        got, ok = fetch_section(section, cfg)
        items.extend(got)
        complete = complete and ok

    old_items = (old or {}).get("items", {})
    digest_pages = int(cfg["watch"]["digest_pages"])
    tracked = [it for it in items if it["page"] <= digest_pages]
    log(f"需要抓取正文指纹的条目：{len(tracked)} / {len(items)}（各栏目前 {digest_pages} 页）")

    confirmed = 0
    for idx, it in enumerate(tracked, 1):
        prev = old_items.get(it["id"], {})
        info, body_changed, pending = detect_body_change(it, prev, cfg)
        it.update(info)
        it["hashes"] = sorted(known_hashes(prev) | ({it["hash"]} if it["hash"] else set()))[-MAX_KNOWN_HASHES:]
        it["pending_hash"] = pending
        it["digest_tracked"] = True
        it["body_changed"] = body_changed
        confirmed += 1 if body_changed else 0
        if idx % 15 == 0:
            log(f"  正文进度 {idx}/{len(tracked)}")
        time.sleep(0.15)

    if confirmed:
        log(f"其中 {confirmed} 篇的正文改动已确认（连续两次取样一致且跨运行稳定）")

    # 较早的公告只比对标题，不抓正文，节省请求（沿用基线里的信息）
    for it in items:
        if it["page"] > digest_pages:
            prev = old_items.get(it["id"], {})
            it["hash"] = prev.get("hash", "")
            it["hashes"] = sorted(known_hashes(prev))[-MAX_KNOWN_HASHES:]
            it["pending_hash"] = prev.get("pending_hash", "")
            it["chars"] = prev.get("chars", 0)
            it["summary"] = prev.get("summary", "")
            it["publish"] = prev.get("publish", "")
            it["source"] = prev.get("source", "")
            it["digest_tracked"] = False
            it["body_changed"] = False

    _write_partial(items, digest_pages)
    return items, complete


def _write_partial(items: list[dict], digest_pages: int) -> None:
    """把本轮抓取结果落到 state/partial.json.gz，便于调试与人工核查。

    在 CI 里默认跳过：这份文件只是给人看的，没必要每次提交进仓库。
    """
    if IS_CI and not os.environ.get("JSZG_WRITE_PARTIAL"):
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _write_json_gz(
            PARTIAL_PATH,
            {"saved_at": now_cst().strftime("%Y-%m-%d %H:%M:%S"),
             "total": len(items), "digest_pages": digest_pages, "items": items},
        )
    except Exception as exc:  # noqa: BLE001
        log(f"警告：partial.json.gz 写入失败（{exc}）")


# --------------------------------------------------------------------------
# 对比
# --------------------------------------------------------------------------

def diff_snapshots(old: dict, items: list[dict], cfg: dict) -> dict:
    """以文章 ID 为唯一键与基线对比，产出本轮的报告输入。

    下架判定故意做得很保守：该站点列表会整条丢失某篇公告（缓存/水位抖动），
    所以只有「连续 N 天都抓不到」才确认下架；期间如果又抓到，计时清零。
    """
    old_items = (old or {}).get("items", {})
    history = dict(old.get("removed_history") or {})
    new_map = {it["id"]: it for it in items}
    today = now_cst().strftime("%Y-%m-%d")
    confirm_days = max(1, int(cfg["watch"].get("removal_confirm_days") or 7))

    added, changed, removed, reappeared = [], [], [], []
    restored = []
    for aid, item in new_map.items():
        if aid in history:
            reappeared.append({**item, "removed_at": history[aid].get("removed_at", "")})
            history.pop(aid, None)
        prev = old_items.get(aid)
        if prev is None:
            # 基线里从没见过 = 真正的新公告
            added.append(item)
            continue
        if prev.get("first_missing_at"):
            # 之前某轮列表里没抓到、现在又出现：站点缓存抖动，不是新增
            restored.append({**item, "missing_since": prev["first_missing_at"]})
        fields = []
        if (prev.get("title") or "") != (item.get("title") or ""):
            fields.append(f"标题：{prev.get('title')} → {item.get('title')}")
        if item.get("body_changed"):
            fields.append("公告正文有改动")
        if fields:
            changed.append({**item, "changes": fields})

    missing: dict[str, str] = {}
    for aid, prev in old_items.items():
        if aid in new_map:
            continue
        since = prev.get("first_missing_at") or today
        if _days_between(since, today) >= confirm_days:
            removed.append(prev)
            history[aid] = {
                "title": prev.get("title", ""),
                "url": prev.get("url", ""),
                "date": prev.get("date", ""),
                "section": prev.get("section", ""),
                "removed_at": today,
            }
        else:
            missing[aid] = since

    return {
        "added": added,
        "changed": changed,
        "removed": removed,
        "reappeared": reappeared,
        "restored": restored,
        "missing": missing,
        "removed_history": history,
    }


def _days_between(start: str, end: str) -> int:
    try:
        a = datetime.strptime(start, "%Y-%m-%d")
        b = datetime.strptime(end, "%Y-%m-%d")
        return (b - a).days
    except Exception:  # noqa: BLE001
        return 0


def split_by_province(items: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    kws = cfg["watch"]["province_keywords"]
    hit = [i for i in items if matches_keywords(i.get("title", ""), kws)]
    other = [i for i in items if not matches_keywords(i.get("title", ""), kws)]
    return hit, other


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------

def build_report(checked_at: str, total: int, diff: dict, cfg: dict, base_date: str | None,
                 first_run: bool) -> dict:
    added, changed, removed = diff["added"], diff["changed"], diff["removed"]
    reappeared = diff.get("reappeared") or []
    restored = diff.get("restored") or []
    zj_added, other_added = split_by_province(added, cfg)
    zj_changed, other_changed = split_by_province(changed, cfg)
    extra = [
        i for i in added + changed
        if matches_keywords(i.get("title", ""), cfg["watch"]["extra_keywords"])
    ]

    has_change = bool(added or changed or removed or reappeared)
    if zj_added or zj_changed:
        level, headline = "critical", "浙江省教师资格认定公告有更新"
    elif has_change:
        level, headline = "info", "通知公告有更新（非浙江）"
    elif first_run:
        level, headline = "baseline", "已建立监控基线"
    else:
        level, headline = "none", "通知公告无新增、无变化"

    L: list[str] = []
    L.append("# 教师资格网 · 通知公告变更日报")
    L.append("")
    L.append(f"- 检查时间：{checked_at}")
    L.append(f"- 对比基线：{base_date or '（无，本次为首次运行）'}")
    L.append(f"- 监控栏目：{'、'.join(s['name'] for s in SECTIONS)}")
    L.append(f"- 当前公告总数：{total}")
    L.append("")

    if first_run:
        L.append("## 首次运行 · 已建立基线")
        L.append("")
        L.append("本次把当前全部公告存为基线快照，**不会**把历史公告当作新增。")
        L.append("从下一次运行开始，即可对比出「比昨天新增 / 变化」的公告。")
        L.append("")

    if zj_added:
        L.append("## ⭐ 浙江省相关 · 新增公告")
        L.append("")
        for it in zj_added:
            L.append(f"- **[{it['title']}]({it['url']})**")
            L.append(f"  - 发布：{it['date']} ｜ 栏目：{it['section']}")
        L.append("")

    if zj_changed:
        L.append("## ⭐ 浙江省相关 · 内容有更新")
        L.append("")
        for it in zj_changed:
            L.append(f"- **[{it['title']}]({it['url']})**")
            L.extend(f"  - {c}" for c in it.get("changes", []))
        L.append("")

    if other_added:
        L.append(f"## 其他省份 · 新增公告（{len(other_added)} 条）")
        L.append("")
        for it in other_added:
            L.append(f"- [{it['title']}]({it['url']}) — {it['date']} ｜ {it['section']}")
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
            L.append(f"- {it['title']} — {it['date']} ｜ {it['section']}")
        L.append("")

    if extra:
        L.append("## 关注关键词命中")
        L.append("")
        for it in extra:
            L.append(f"- {it['title']} — {it['date']}")
        L.append("")

    if reappeared:
        L.append(f"## 之前疑似下架 · 现已重新出现（{len(reappeared)} 条）")
        L.append("")
        L.append("说明：该站点列表存在多版本缓存，公告偶尔会整条查不到。")
        L.append("下面这些是「上次没抓到、这次又出现」的公告，按新增处理前请留意。")
        L.append("")
        for it in reappeared:
            L.append(f"- [{it['title']}]({it['url']}) — {it['date']} ｜ {it['section']}")
        L.append("")

    if restored:
        L.append(f"## 暂时查不到 · 现已恢复（{len(restored)} 条）")
        L.append("")
        L.append("这些公告之前某一轮列表里恰好缺失、本轮又出现了，属于站点缓存抖动，")
        L.append("**不是**新增内容，仅作记录。")
        L.append("")
        for it in restored:
            L.append(f"- [{it['title']}]({it['url']}) — {it['date']} ｜ {it['section']}"
                     f"（缺于 {it.get('missing_since')}）")
        L.append("")

    if not has_change and not first_run:
        L.append("## 结论")
        L.append("")
        L.append("与基线相比，通知公告**没有新增、没有修改、没有下架**。")
        L.append("")

    L.append("---")
    L.append(f"<sub>由 jszg_watch.py 自动生成 · 数据来源 [中国教师资格网]({NEWS_PAGE})</sub>")

    # 微信推送摘要（纯文本）
    S = [headline, f"检查时间：{checked_at}"]
    if zj_added:
        S += ["", "【浙江·新增】"] + [f"· {i['title']}（{i['date']}）" for i in zj_added[:6]]
    if zj_changed:
        S += ["", "【浙江·更新】"] + [f"· {i['title']}" for i in zj_changed[:6]]
    if other_added:
        S += ["", f"【其他新增 {len(other_added)} 条】"] + [f"· {i['title']}" for i in other_added[:6]]
    if other_changed:
        S += ["", f"【其他变化 {len(other_changed)} 条】"] + [f"· {i['title']}" for i in other_changed[:6]]
    if removed:
        S += ["", f"【下架 {len(removed)} 条】"] + [f"· {i['title']}" for i in removed[:6]]
    if reappeared:
        S += ["", f"【疑似下架后又出现 {len(reappeared)} 条】"] + [
            f"· {i['title']}" for i in reappeared[:6]
        ]
    if restored:
        S += ["", f"【缓存抖动·暂时查不到又恢复 {len(restored)} 条】"]
    if first_run:
        S += ["", "本次为首次运行，已建立基线，明日起开始对比。"]
    elif not has_change:
        S += ["", "与基线一致，无需处理。"]
    if not IS_CI:
        # 本地跑才提示本地报告路径；CI 上这个路径在别人的机器上没用
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
            "restored": len(restored),
            "zhejiang_added": len(zj_added),
            "zhejiang_changed": len(zj_changed),
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
        f"<title>教师资格网通知公告日报</title><style>{css}</style></head>"
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

def http_post(url: str, data: dict, timeout: int = 30) -> str:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def push_serverchan(cfg: dict, title: str, body: str) -> tuple[bool, str]:
    key = (cfg["channels"]["serverchan"].get("sendkey") or "").strip()
    if not key:
        return False, "未配置 sendkey"
    host = "https://sc.ftqq.com" if key.upper().startswith("SCU") else "https://sctapi.ftqq.com"
    try:
        txt = http_post(f"{host}/{key}.send", {"title": title, "desp": body})
        flat = txt.replace(" ", "")
        return ('"code":0' in flat or '"errno":0' in flat), txt[:300]
    except Exception as exc:  # noqa: BLE001
        return False, f"Server酱推送异常：{exc}"


def push_pushplus(cfg: dict, title: str, body: str) -> tuple[bool, str]:
    token = (cfg["channels"]["pushplus"].get("token") or "").strip()
    if not token:
        return False, "未配置 token"
    try:
        txt = http_post(
            "https://www.pushplus.plus/send",
            {"token": token, "title": title, "content": body, "template": "markdown"},
        )
        return ('"code":200' in txt.replace(" ", "")), txt[:300]
    except Exception as exc:  # noqa: BLE001
        return False, f"PushPlus 推送异常：{exc}"


def notify(cfg: dict, title: str, body: str, dry_run: bool = False) -> list[str]:
    results: list[str] = []
    for label, key, fn in (
        ("Server酱", "serverchan", push_serverchan),
        ("PushPlus", "pushplus", push_pushplus),
    ):
        conf = cfg["channels"].get(key, {})
        if not conf.get("enabled"):
            continue
        if dry_run:
            results.append(f"[dry-run] 跳过 {label} 推送")
            continue
        ok, msg = fn(cfg, title, body)
        line = f"{label}: {'成功' if ok else '失败'} {msg}"
        results.append(line)
        log(line)

    if not results:
        results.append("未启用任何推送渠道（可在 config.json 里开启 Server酱 / PushPlus）")
        log(results[-1])
    return results


def windows_toast(title: str, message: str) -> None:
    """可选的 Windows 桌面气泡提醒；失败不影响主流程。

    只在本地 Windows 上弹，CI（GitHub Actions）里直接跳过。
    """
    if IS_CI or os.name != "nt":
        return
    try:
        safe_title = title.replace("'", "''")
        safe_msg = message.replace("'", "''")
        ps = (
            "[reflection.assembly]::loadwithpartialname('System.Windows.Forms')|Out-Null;"
            "[reflection.assembly]::loadwithpartialname('System.Drawing')|Out-Null;"
            "$n=New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
            f"$n.ShowBalloonTip(10000,'{safe_title}','{safe_msg}',0);"
            "Start-Sleep -Seconds 8;$n.Dispose()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


# --------------------------------------------------------------------------
# 快照读写
# --------------------------------------------------------------------------

def _read_json_maybe_gz(path: Path):
    """读 JSON，自动识别 .gz（快照用 gzip 存，每天提交到仓库时体积小得多）。"""
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return json.loads(data.decode("utf-8"))


def _write_json_gz(path: Path, obj) -> None:
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(payload, 9, mtime=0))  # mtime=0 → 内容相同则字节相同
    tmp.replace(path)


def load_snapshot() -> dict:
    for path in (SNAPSHOT_PATH, STATE_DIR / "snapshot.json"):  # 兼容旧的未压缩快照
        if path.exists():
            try:
                return _read_json_maybe_gz(path)
            except Exception as exc:  # noqa: BLE001
                log(f"警告：{path.name} 读取失败（{exc}），按首次运行处理")
    return {}


def write_snapshot(merged: dict, checked_at: str, missing: dict | None = None,
                   history: dict | None = None, note: str = "", runs: int = 1) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 3,
        "checked_at": checked_at,
        "total": len(merged),
        "note": note,
        "run_count": runs,
        "missing": missing or {},
        "removed_history": history or {},
        "items": merged,
    }
    _write_json_gz(SNAPSHOT_PATH, data)
    # 老版本用的是未压缩的 state/snapshot.json，迁移后删掉以免两份并存
    legacy = STATE_DIR / "snapshot.json"
    if legacy.exists() and SNAPSHOT_PATH != legacy:
        try:
            legacy.unlink()
            log("已把旧的 state/snapshot.json 迁移为 state/snapshot.json.gz")
        except Exception:
            pass


def save_snapshot(old: dict, items: list[dict], state: dict, checked_at: str,
                  note: str = "", incomplete: bool = False) -> None:
    """保存快照。

    `state` 里带两样东西：
      · missing         — 最近没在列表里出现、但还没到确认下架天数的公告
                          「第一次没抓到的时间」，以及
      · removed_history — 已确认下架、之后若重新出现需要特别提示的公告。

    incomplete=True（本轮抓取不完整）时，把这次没抓到、但基线里还有的条目
    合并回来，避免一次网络失败就把大量历史公告误判为新增或下架。
    """
    merged = {it["id"]: it for it in items}
    missing = dict(state.get("missing") or {})
    old_items = (old or {}).get("items", {})
    for aid, prev in old_items.items():
        if aid in merged:
            continue
        keep = dict(prev)
        if aid in missing:
            keep["first_missing_at"] = missing[aid]
        merged[aid] = keep
    runs = int((old or {}).get("run_count") or 0) + 1
    write_snapshot(merged, checked_at, missing, state.get("removed_history") or {}, note, runs)
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


def prune_reports(keep_last: int) -> None:
    if not keep_last or keep_last <= 0 or not REPORT_DIR.exists():
        return
    files = sorted(
        (p for p in REPORT_DIR.glob("*") if p.name not in ("latest.md", "latest.html")),
        key=lambda p: p.stat().st_mtime,
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
    log("=" * 62)
    log(f"开始检查 {NEWS_PAGE}")

    old = load_snapshot()
    first_run = not old.get("items")
    base_date = old.get("checked_at")
    if first_run and not args.baseline:
        log("注意：没有找到快照。在 GitHub Actions 上通常说明 state/ 没被拉取到，")
        log("      或仓库里还没有提交过 state/snapshot.json.gz。本次将建立新基线、不推送。")

    try:
        items, complete = collect(cfg, {} if (first_run or args.baseline) else old)
    except Exception as exc:  # noqa: BLE001
        log(f"抓取失败：{exc}")
        if args.dry_run:
            traceback.print_exc()
        notify(
            cfg,
            "教师资格网｜抓取失败",
            f"抓取 {NEWS_PAGE} 失败：{exc}\n请检查网络或稍后重试。",
            dry_run=args.dry_run,
        )
        return 2

    if not items:
        log("没有解析到任何公告，页面结构可能已变化")
        return 3
    log(f"本次共解析到 {len(items)} 条公告{'（完整）' if complete else '（不完整）'}")

    missing = dict(old.get("missing") or {})

    if first_run or args.baseline:
        diff = {"added": [], "changed": [], "removed": [], "reappeared": [], "restored": []}
        report = build_report(checked_at, len(items), diff, cfg, None, first_run=True)
        log("已建立基线快照（本次不产生变更告警）")
        if not args.dry_run:
            # 基线只增不减：把旧快照里这次没抓到的条目也保留下来，
            # 免得站点缓存恰好藏起某条公告时，下次运行把它误报成新增。
            save_snapshot(old, items, {"missing": {}, "removed_history": {}}, checked_at,
                          note="baseline", incomplete=not complete)
    else:
        diff = diff_snapshots(old, items, cfg)
        for aid in diff["missing"]:
            prev = (old.get("items") or {}).get(aid, {})
            log(f"  {prev.get('title', aid)}：最近未在列表中出现（自 {diff['missing'][aid]} 起），暂不告警")
        report = build_report(checked_at, len(items), diff, cfg, base_date, first_run=False)
        zj_add = sum(1 for i in diff["added"] if matches_keywords(i["title"], cfg["watch"]["province_keywords"]))
        zj_chg = sum(1 for i in diff["changed"] if matches_keywords(i["title"], cfg["watch"]["province_keywords"]))
        log(
            f"对比结果：新增 {len(diff['added'])}，变化 {len(diff['changed'])}，"
            f"下架 {len(diff['removed'])}，重新出现 {len(diff['reappeared'])}；"
            f"其中浙江 新增 {zj_add}，更新 {zj_chg}"
        )
        if not args.dry_run:
            save_snapshot(
                old, items,
                {"missing": diff["missing"], "removed_history": diff["removed_history"]},
                checked_at, incomplete=not complete,
            )

    stamp = now_cst().strftime("%Y%m%d-%H%M%S")
    if not args.dry_run:
        path = save_reports(report, stamp)
        log(f"报告已保存：{path}")
        prune_reports(int(cfg["report"].get("keep_last") or 0))

    print("-" * 62)
    print(report["markdown"])
    print("-" * 62)

    counts = report["counts"]
    zj_hit = bool(counts["zhejiang_added"] or counts["zhejiang_changed"])
    non_zj_hit = bool(report["has_change"]) and not zj_hit
    should_push = args.force or cfg["notify"].get("always_notify", False) or zj_hit or (
        non_zj_hit and cfg["notify"].get("notify_non_zhejiang", True)
    )
    if first_run and not args.force:
        log("首次运行只建立基线，不推送；如需测试推送请加 --force")
        should_push = False

    if should_push:
        notify(cfg, f"教师资格网｜{report['headline']}", report["summary"], dry_run=args.dry_run)
        if zj_hit and not args.dry_run:
            windows_toast("教师资格网：浙江省有更新", "发现浙江相关变更，请查看微信推送")
    else:
        log("无变化或未命中推送条件，不发送通知")

    log("检查完成")
    return 0


def self_test() -> int:
    cfg = load_config()
    print("== 自检 ==")
    print(f"程序目录   : {BASE_DIR}")
    print(f"配置文件   : {CONFIG_PATH}  [{'存在' if CONFIG_PATH.exists() else '缺失，将用默认配置'}]")
    print(f"快照文件   : {SNAPSHOT_PATH}  [{'存在' if SNAPSHOT_PATH.exists() else '尚未创建'}]")
    for name, conf in cfg["channels"].items():
        state = "已启用" if conf.get("enabled") else "未启用"
        print(f"推送渠道   : {name} — {state}")

    print("\n-- 网络连通性 --")
    try:
        raw = http_get(NEWS_PAGE, cfg)
        print(f"news.html 访问正常（{len(raw)} 字节）")
    except Exception as exc:  # noqa: BLE001
        print(f"news.html 访问失败：{exc}")
        return 1

    print("\n-- 各栏目抓取 --")
    total = 0
    all_ok = True
    for section in SECTIONS:
        try:
            items, ok = fetch_section(section, cfg)
            all_ok = all_ok and ok
            zj = [i for i in items if matches_keywords(i["title"], cfg["watch"]["province_keywords"])]
            total += len(items)
            print(f"  {section['name']}：{len(items)} 条，其中浙江相关 {len(zj)} 条")
            for i in zj[:8]:
                print(f"      · {i['date']}  {i['title']}")
        except Exception as exc:  # noqa: BLE001
            all_ok = False
            print(f"  {section['name']}：抓取失败 {exc}")
    print(f"合计 {total} 条")
    if not total:
        return 1
    print("\n自检通过。" if all_ok else "\n自检完成，但部分分页抓取不完整，请检查网络。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="中国教师资格网 通知公告 每日变更监控（重点关注浙江省）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只检查并打印，不推送、不写文件")
    parser.add_argument("--baseline", action="store_true", help="把当前状态设为基线，不产生告警")
    parser.add_argument("--force", action="store_true", help="强制推送一次（测试推送通道用）")
    parser.add_argument("--self-test", action="store_true", help="自检：配置、网络、栏目抓取")
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
