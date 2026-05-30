"""
验证脚本：测试 OP.GG stats provider，以船长(Gangplank) 上路为例。
同时加载 DDragon 把 item/rune ID 翻译成名字。
"""
import json
import sys
from pathlib import Path

from data.ddragon import DDragonClient
from stats.opgg import OpggProvider

CACHE_DIR = Path("cache")


def name_items(ids: list[int], item_map: dict) -> str:
    if not ids:
        return "（无）"
    return " → ".join(item_map.get(i, str(i)) for i in ids)


def main():
    print("══════════════════════════════════════════════")
    print(" verify_stats.py  —  OP.GG 数据验证")
    print("══════════════════════════════════════════════\n")

    # 1. DDragon 装备名映射
    dd = DDragonClient(cache_dir=CACHE_DIR)
    version = dd.ensure_loaded()
    print(f"  DDragon 版本: {version}")

    item_json_path = CACHE_DIR / f"ddragon_items_{version}_zh_CN.json"
    item_map: dict[int, str] = {}
    if item_json_path.exists():
        raw = json.loads(item_json_path.read_text(encoding="utf-8"))
        for item_id_str, entry in raw.get("data", {}).items():
            item_map[int(item_id_str)] = entry.get("name", item_id_str)
    else:
        # 尝试直接从 DDragon 拉装备
        print("  [警告] 装备缓存不存在，ID 将以数字显示")

    # 2. 查船长 TOP
    champ, role = "Gangplank", "top"
    print(f"\n查询: {champ} / {role}  (tier=gold_plus)\n")

    provider = OpggProvider(tier="gold_plus", debug=True)

    build = provider.get_build(champ, role)
    runes = provider.get_runes(champ, role)

    # ── 出装 ──────────────────────────────────────────
    print("▶ 出装")
    if not build:
        print("  获取失败")
    else:
        if build.stale:
            print(f"  ⚠ 数据可能过期: {build.stale_reason}")
        print(f"  起手 : {name_items(build.starter, item_map)}")
        print(f"  鞋子 : {name_items(build.boots,   item_map)}")
        print(f"  核心 : {name_items(build.core,    item_map)}")
        print(f"  四件 : {name_items(build.fourth,  item_map)}")
        print(f"  五件 : {name_items(build.fifth,   item_map)}")
        print(f"  六件 : {name_items(build.sixth,   item_map)}")
        print(f"  来源 : {build.source}")
        print()
        print(f"  [原始 ID] 起手={build.starter} 鞋={build.boots} 核心={build.core}")
        print(f"            四件={build.fourth} 五件={build.fifth} 六件={build.sixth}")

    # ── 符文 ──────────────────────────────────────────
    print("\n▶ 符文")
    if not runes:
        print("  获取失败")
    else:
        if runes.stale:
            print(f"  ⚠ 数据可能过期: {runes.stale_reason}")
        print(f"  主系树 ID    : {runes.primary_tree}")
        print(f"  副系树 ID    : {runes.secondary_tree}")
        print(f"  核心符文     : {runes.keystone}")
        print(f"  主系其他符文 : {runes.primary_perks}")
        print(f"  副系符文     : {runes.secondary_perks}")
        print(f"  来源         : {runes.source}")

    print("\n══════════════════════════════════════════════")
    print(" 验证完成")
    print("══════════════════════════════════════════════")


if __name__ == "__main__":
    main()
