#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CI 冒烟测试 —— 在 GitHub Actions 上验证抓取与对比逻辑仍然可用。

设计原则：
    · 除了最后一节「联网检查」，其余全部离线运行，不依赖外网；
    · 只做只读操作，不写快照、不推送、不修改仓库里的任何文件
      （快照读写用临时目录）；
    · 联网失败时明确打印「跳过」，不让站点临时抽风就把整个仓库变红。

用法：python tools/ci_smoke_test.py
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []
skipped: list[str] = []


def ok(msg: str) -> None:
    print(f"  \u2713 {msg}")


def fail(msg: str) -> None:
    print(f"  \u2717 {msg}")
    failures.append(msg)


def skip(msg: str) -> None:
    print(f"  - 跳过：{msg}")
    skipped.append(msg)


def load_module():
    spec = importlib.util.spec_from_file_location("jszg_watch", ROOT / "jszg_watch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 真实列表页的精简样本（格式与 zjzs.net 的 <datastore><record><![CDATA[...]]> 一致）
LIST_SAMPLE = """
<div id="100"><script type="text/xml"><datastore>
<nextgroup><![CDATA[<a href="/module/web/jpage/dataproxy.jsp?page=1&webid=1&path=/&columnid=62&unitid=100&permissiontype=0"></a>]]></nextgroup>
<recordset>
<ul class="zjhz-tabcut-content" data-role="tab-content"></ul>
<record><![CDATA[
  <li>    <a      href="/art/2026/9/14/art_62_12627.html"      target="_blank"
      title="浙江省2026年中小学教师资格定期注册公告"      class="zjhz-tabcut-content-title"      >浙江省2026年中小学教师资格定期注册公告</a
    >    <span class="zjhz-tabcut-content-titme"      >2026-09-14</span    >  </li>  ]]></record>
<record><![CDATA[
  <li>    <a      href="/art/2026/3/10/art_62_12100.html"      target="_blank"
      title="浙江省2026年中小学教师资格认定公告"      class="zjhz-tabcut-content-title"      >浙江省2026年中小学教师资格认定公告</a
    >    <span class="zjhz-tabcut-content-titme"      >2026-03-10</span    >  </li>  ]]></record>
</recordset></datastore></script></div>
<script>var param_100 = {col:1,webid:1,columnid:62,unitid:'100'};
function jpage_100(){param_100.totalRecord=2;}</script>
<script>$(function(){$('#100').jpage({totalRecord:2,perPage:12,proxyUrl:'/module/web/jpage/dataproxy.jsp'});});</script>
"""

ARTICLE_SAMPLE = """
<html><head>
<meta name="ArticleTitle" content="浙江省2026年中小学教师资格认定公告">
<meta name="pubdate" content="2026-03-10 10:00"/>
<meta name="guid" content="20260012100">
</head><body>
<div class="zjhz-article-box">
  <h1 class="zjhz-main_title">浙江省2026年中小学教师资格认定公告</h1>
  <div class="zjhz-artic_tother clearfix"><p class="zjhz-time">发布时间：2026-03-10 10:00</p></div>
  <div class="zjhz-article-main_section">
     <p>根据《中华人民共和国教师法》等规定，现将浙江省2026年中小学教师资格认定有关事项公告如下。</p>
     <p>一、认定对象 …… 二、时间安排 ……</p>
  </div>
  <div class="zjhz-artic_qr-code">扫一扫</div>
</div></body></html>
"""


def main() -> int:
    jw = load_module()
    cfg = jw.load_config()

    print("== 1. 配置与推送渠道 ==")
    for name, conf in cfg["channels"].items():
        print(f"  渠道 {name}: {'启用' if conf.get('enabled') else '未启用'}")
    # 冒烟测试绝不允许真的发消息
    for conf in cfg["channels"].values():
        conf["enabled"] = False
    print(f"  监控栏目: {cfg['watch'].get('column_name')}")
    print(f"  列表地址: {cfg['watch'].get('list_url')}")

    print("\n== 2. 列表解析（离线） ==")
    items = jw.parse_list(LIST_SAMPLE)
    if len(items) != 2:
        fail(f"列表解析条数不对：{len(items)}（应为 2）")
    else:
        ok("parse_list 解析出 2 条")
    if items and items[0]["id"] != "art_62_12627":
        fail(f"文章 ID 解析错误：{items[0]['id']}")
    else:
        ok("文章 ID 取 art_62_12627")
    if items and items[1]["date"] != "2026-03-10":
        fail(f"日期解析错误：{items[1]['date']}")
    else:
        ok("发布日期解析正确")

    print("\n== 3. 正文解析（离线） ==")
    info = jw.parse_article(ARTICLE_SAMPLE)
    if info["chars"] <= 0 or not info["hash"]:
        fail("正文解析为空")
    else:
        ok(f"正文解析 {info['chars']} 字，指纹 {info['hash'][:12]}…")
    if info["publish"] != "2026-03-10":
        fail(f"发布时间解析错误：{info['publish']}")
    else:
        ok("发布时间取 meta pubdate")
    if "扫一扫" in info["summary"]:
        fail("正文把页脚二维码也算进去了")
    else:
        ok("正文正确排除了页脚/二维码")

    print("\n== 4. 快照压缩读写（离线） ==")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "snapshot.json.gz"
        sample = {"version": 4, "site": jw.SITE, "total": 2, "missing": {}, "items": {"a1": {"id": "a1"}}}
        jw._write_json_gz(path, sample)
        if path.read_bytes()[:2] != b"\x1f\x8b":
            fail("快照没有写成 gzip")
        else:
            ok(f"写出的快照是 gzip（{path.stat().st_size} 字节）")
        if jw._read_json_gz(path) != sample:
            fail("gzip 快照读回来内容不一致")
        else:
            ok("gzip 快照往返一致")

    print("\n== 5. 对比逻辑（离线） ==")
    old = {
        "site": jw.SITE,
        "checked_at": "2026-01-01 09:00:00",
        "missing": {},
        "removed_ids": ["art_62_9999"],
        "items": {
            "art_62_12100": {"id": "art_62_12100", "title": "浙江省2026年中小学教师资格认定公告",
                             "date": "2026-03-10"},
            "art_62_8888": {"id": "art_62_8888", "title": "某条早已下架的公告",
                            "date": "2020-01-01", "first_missing_at": "2020-01-01"},
        },
    }
    fresh = [
        {"id": "art_62_12100", "title": "浙江省2026年中小学教师资格认定公告", "date": "2026-03-10"},
        {"id": "art_62_12627", "title": "浙江省2026年下半年中小学教师资格认定公告", "date": "2026-09-16"},
        {"id": "art_62_9999", "title": "曾经下架又上架的公告", "date": "2021-01-01"},
    ]
    diff = jw.diff_snapshots(old, fresh, cfg, complete=True)
    if [i["id"] for i in diff["added"]] != ["art_62_12627"]:
        fail(f"新增判定不对：{[i['id'] for i in diff['added']]}（应只有 art_62_12627）")
    else:
        ok("基线里没有的 ID 才判为新增")
    if [i["id"] for i in diff["reappeared"]] != ["art_62_9999"]:
        fail(f"重新出现判定不对：{[i['id'] for i in diff['reappeared']]}")
    else:
        ok("已确认下架后重新出现 → 归入「重新出现」，不计新增")
    if [i["id"] for i in diff["removed"]] != ["art_62_8888"]:
        fail(f"下架判定不对：{[i['id'] for i in diff['removed']]}")
    else:
        ok("超过确认天数的缺失公告才判为下架")
    admin_focus, _ = jw.split_by_focus(diff["added"], cfg)
    if [i["id"] for i in admin_focus] != ["art_62_12627"]:
        fail("重点关键词分组不正确（下半年认定公告应命中「教师资格认定」）")
    else:
        ok("重点关键词分组正确")

    # 抓取不完整时不应确认下架
    diff_incomplete = jw.diff_snapshots(old, fresh[:1], cfg, complete=False)
    if diff_incomplete["removed"]:
        fail("抓取不完整时仍然判定下架（应跳过）")
    else:
        ok("抓取不完整时不做下架判定")

    # 标题变化
    renamed = [dict(fresh[0], title="浙江省2026年中小学教师资格认定公告（修订）")]
    diff2 = jw.diff_snapshots(old, renamed, cfg, complete=True)
    if not diff2["changed"]:
        fail("标题变化没有被识别")
    else:
        ok("标题变化被识别为「变化」")

    print("\n== 6. 快照合并：确认下架的条目应被移除（离线） ==")
    with tempfile.TemporaryDirectory() as tmp:
        jw.STATE_DIR = Path(tmp)
        jw.SNAPSHOT_PATH = Path(tmp) / "snapshot.json.gz"
        jw.save_snapshot(old, fresh[:1], diff, "2026-01-01 09:00:00")
        saved = jw._read_json_gz(jw.SNAPSHOT_PATH)
        if "art_62_8888" in saved["items"]:
            fail("已确认下架的公告仍留在快照里（会导致每天重复告警）")
        else:
            ok("已确认下架的公告已从快照移除")
        if "art_62_12100" not in saved["items"]:
            fail("本轮抓到的公告没有写进快照")
        else:
            ok("本轮抓到的公告已写进快照")

    print("\n== 7. 推送未启用时不应发消息（离线） ==")
    result = jw.notify(cfg, "冒烟测试", "不应发出", dry_run=True)
    if not result:
        fail("notify() 没有返回任何结果")
    else:
        ok(f"notify() 返回：{result[0]}")

    print("\n== 8. 联网检查 ==")
    try:
        live_items, complete = jw.fetch_list(cfg)
        ok(f"列表抓取成功（{len(live_items)} 条，完整={complete}）")
        if not live_items:
            fail("联网抓取没有解析到任何公告，页面结构可能已变化")
        else:
            titles = [i["title"] for i in live_items]
            if not any("教师资格" in t for t in titles):
                fail("列表里没有教师资格相关公告，标题解析可能失效")
            else:
                ok("能解析出教师资格相关公告")
            if live_items[0]["id"].startswith("art_"):
                ok(f"最新一条 ID：{live_items[0]['id']}（{live_items[0]['date']}）")
            else:
                fail(f"文章 ID 格式异常：{live_items[0]['id']}")
    except Exception as exc:  # noqa: BLE001
        skip(f"列表抓取失败：{exc}")

    print("\n" + "=" * 60)
    if skipped:
        print(f"跳过 {len(skipped)} 项（外网不可用，不算失败）：")
        for s in skipped:
            print(f"  - {s}")
    if failures:
        print(f"失败 {len(failures)} 项：")
        for f in failures:
            print(f"  ! {f}")
        return 1
    print("冒烟测试通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
