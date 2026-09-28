#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CI 冒烟测试 —— 在 GitHub Actions 上验证抓取与对比逻辑仍然可用。

设计原则：
    · 只做只读操作，不写快照、不推送、不修改仓库里的任何文件；
    · 允许网络失败时跳过需要联网的检查，但必须把「跳过」明确打出来，
      避免站点临时抽风就让整个仓库变红；
    · 重点验证纯逻辑（快照压缩读写、新增/恢复/下架的判定），这部分
      不依赖外网，一定跑得起来。

用法：python tools/ci_smoke_test.py
"""

from __future__ import annotations

import importlib.util
import json
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


def main() -> int:
    jw = load_module()
    cfg = jw.load_config()

    print("== 1. 配置与推送渠道 ==")
    for name, conf in cfg["channels"].items():
        state = "启用" if conf.get("enabled") else "未启用"
        print(f"  渠道 {name}: {state}")
    # 冒烟测试绝不允许真的发消息
    for conf in cfg["channels"].values():
        conf["enabled"] = False

    print("\n== 2. 快照压缩读写（离线） ==")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "snapshot.json.gz"
        sample = {
            "version": 3,
            "checked_at": "2026-01-01 09:00:00",
            "total": 2,
            "missing": {"a2": "2026-01-01"},
            "removed_history": {},
            "items": {
                "a1": {"id": "a1", "title": "浙江省2026年中小学教师资格认定公告",
                       "date": "2026-03-16", "hashes": ["deadbeef"], "digest_tracked": True},
                "a2": {"id": "a2", "title": "某省公告", "date": "2026-01-01"},
            },
        }
        jw._write_json_gz(path, sample)
        if path.read_bytes()[:2] != b"\x1f\x8b":
            fail("快照没有写成 gzip")
        else:
            ok(f"写出的快照是 gzip（{path.stat().st_size} 字节）")
        back = jw._read_json_maybe_gz(path)
        if back != sample:
            fail("gzip 快照读回来内容不一致")
        else:
            ok("gzip 快照往返一致")

        # 兼容旧的未压缩快照
        plain = Path(tmp) / "snapshot.json"
        plain.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
        if jw._read_json_maybe_gz(plain) != sample:
            fail("读取未压缩快照失败（兼容性坏了）")
        else:
            ok("仍能读取旧的未压缩快照")

    print("\n== 3. 对比逻辑（离线） ==")
    old = {
        "checked_at": "2026-01-01 09:00:00",
        "missing": {},
        "removed_history": {},
        "items": {
            "old1": {"id": "old1", "title": "浙江省2026年中小学教师资格认定公告",
                     "date": "2026-03-16", "section": "教师资格认定"},
            "old2": {"id": "old2", "title": "江苏省2026年上半年教师资格认定公告",
                     "date": "2026-03-20", "section": "教师资格认定",
                     "first_missing_at": "2026-01-01"},
        },
    }
    items = [
        {"id": "old1", "title": "浙江省2026年中小学教师资格认定公告",
         "date": "2026-03-16", "section": "教师资格认定", "digest_tracked": False},
        {"id": "old2", "title": "江苏省2026年上半年教师资格认定公告",
         "date": "2026-03-20", "section": "教师资格认定", "digest_tracked": False},
        {"id": "new1", "title": "浙江省2026年下半年中小学教师资格认定公告",
         "date": "2026-09-16", "section": "教师资格认定", "digest_tracked": False},
    ]
    diff = jw.diff_snapshots(old, items, cfg)
    if [i["id"] for i in diff["added"]] != ["new1"]:
        fail(f"新增判定不对：{[i['id'] for i in diff['added']]}（应只有 new1）")
    else:
        ok("基线里没有的 ID 才判为新增")
    if [i["id"] for i in diff["restored"]] != ["old2"]:
        fail(f"「暂时查不到又恢复」判定不对：{[i['id'] for i in diff['restored']]}")
    else:
        ok("曾经缺失的公告归入「暂时查不到 · 现已恢复」，不计新增")
    zj, other = jw.split_by_province(diff["added"], cfg)
    if [i["id"] for i in zj] != ["new1"] or other:
        fail("浙江分组不正确")
    else:
        ok("浙江关键词分组正确")

    # 长期缺失 → 确认下架
    old2 = json.loads(json.dumps(old))
    old2["items"]["old3"] = {"id": "old3", "title": "已下架的旧公告",
                             "date": "2020-01-01", "first_missing_at": "2020-01-01"}
    diff2 = jw.diff_snapshots(old2, items, cfg)
    if [i["id"] for i in diff2["removed"]] != ["old3"]:
        fail(f"下架判定不对：{[i['id'] for i in diff2['removed']]}")
    else:
        ok("超过确认天数的缺失公告才判为下架")
    if "old3" not in diff2["removed_history"]:
        fail("下架历史没有记录")
    else:
        ok("下架会写入 removed_history")

    print("\n== 4. 推送未启用时不应发消息（离线） ==")
    result = jw.notify(cfg, "冒烟测试", "不应发出", dry_run=True)
    if not result:
        fail("notify() 没有返回任何结果")
    else:
        ok(f"notify() 返回：{result[0]}")

    print("\n== 5. 联网检查 ==")
    try:
        raw = jw.http_get(jw.NEWS_PAGE, cfg)
        ok(f"news.html 可访问（{len(raw)} 字节）")
    except Exception as exc:  # noqa: BLE001
        skip(f"news.html 访问失败：{exc}")
        raw = ""

    if raw:
        try:
            items_now, complete = jw.fetch_section({"id": "107", "name": "教师资格认定"}, cfg)
            if len(items_now) < 100:
                fail(f"教师资格认定栏目只抓到 {len(items_now)} 条，明显偏少（页面结构可能变了）")
            else:
                ok(f"教师资格认定栏目抓到 {len(items_now)} 条（完整={complete}）")
            if not any("浙江" in i["title"] for i in items_now):
                fail("列表里没有解析到任何浙江公告，标题解析可能失效")
            else:
                ok("能解析出浙江相关公告")
        except Exception as exc:  # noqa: BLE001
            skip(f"栏目抓取失败：{exc}")

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
