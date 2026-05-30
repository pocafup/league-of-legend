"""
第4步验证：ID翻译 + 敌方位置推断 + 对位匹配
用法：uv run verify_step4.py
"""
from __future__ import annotations

import json
from pathlib import Path

from data.ddragon import DDragonClient
from advisor.session import DraftSession, TeamMember, enrich_session, parse_lcu_session
from stats.opgg import OpggProvider
from stats.provider import Build, Runes

CACHE_DIR = Path("cache")
FIXTURE   = Path("fixtures/sample_5v5.json")

PHASE_CN = {
    "PLANNING":      "准备阶段",
    "BAN_PICK":      "封禁/选择",
    "FINALIZATION":  "确认锁定",
    "GAME_STARTING": "游戏即将开始",
}
CONF_MARKER = {
    "很可能": "[很可能]",
    "可能":   "[可能]  ",
    "不确定": "[不确定]",
}


# ── 翻译辅助 ────────────────────────────────────────────────────────────────

def tr_items(ids: list[int], dd: DDragonClient) -> str:
    if not ids:
        return "（无）"
    return " → ".join(dd.item_name(i) for i in ids)


def tr_rune(id_: int, dd: DDragonClient) -> str:
    return dd.rune_name(id_) if id_ else "（无）"


def tr_runes_list(ids: list[int], dd: DDragonClient) -> str:
    if not ids:
        return "（无）"
    return " / ".join(dd.rune_name(i) for i in ids)


# ── 打印函数 ─────────────────────────────────────────────────────────────────

def print_teams(ds: DraftSession) -> None:
    phase_label = PHASE_CN.get(ds.phase, ds.phase or "未知")
    game_type   = "自定义对局" if ds.is_custom else "匹配对局"

    print()
    print("══════════════════════════════════════════════════════════")
    print(f"  阵容总览   阶段: {phase_label}   {game_type}")
    print("══════════════════════════════════════════════════════════")

    local = ds.local_member
    if local and local.champ.numeric_id:
        opp = local.opponent
        opp_str = f"  对线对手: {opp.champ.zh_name}（{opp.pos_confidence}）" if opp else ""
        print(f"\n  你: {local.position_cn}  {local.champ.zh_name}（{local.champ.en_id}）{opp_str}")

    print("\n【我方队伍】")
    for m in ds.my_team:
        marker = " ← 你" if m.is_local else ""
        name   = m.champ.zh_name if m.champ.numeric_id else "未选"
        opp_str = f"  对位: {m.opponent.champ.zh_name}" if m.opponent else ""
        print(f"  {m.position_cn:<3}  {name}{marker}{opp_str}")

    print("\n【敌方队伍】（位置为推断）")
    if all(m.champ.numeric_id == 0 for m in ds.enemy_team):
        print("  （敌方数据不可用）")
    else:
        for m in ds.enemy_team:
            conf = CONF_MARKER.get(m.pos_confidence, "        ")
            pos  = m.position_cn if m.inferred_pos else "?"
            print(f"  {pos:<3} {conf}  {m.champ.zh_name}")

    # 对线对手总结
    local_opp = local.opponent if local else None
    print()
    if local_opp:
        print(f"▶ 你的对线对手: {local_opp.champ.zh_name}（{local_opp.pos_confidence}，{local_opp.pos_source}）")
    else:
        print("▶ 无法确定对线对手（推断失败）")

    print("══════════════════════════════════════════════════════════")


def print_build(build: Build | None, runes: Runes | None, dd: DDragonClient) -> None:
    print()
    if build:
        if build.stale:
            print(f"  ⚠ 出装数据可能过期: {build.stale_reason}")
        print(f"▶ 推荐出装  （来源: {build.source}）")
        print(f"  起手 : {tr_items(build.starter, dd)}")
        print(f"  鞋子 : {tr_items(build.boots,   dd)}")
        print(f"  核心 : {tr_items(build.core,    dd)}")
        if build.fourth:
            print(f"  四件 : {tr_items(build.fourth,  dd)}")
        if build.fifth:
            print(f"  五件 : {tr_items(build.fifth,   dd)}")
        if build.sixth:
            print(f"  六件 : {tr_items(build.sixth,   dd)}")
    else:
        print("▶ 出装数据获取失败")

    print()
    if runes:
        if runes.stale:
            print(f"  ⚠ 符文数据可能过期: {runes.stale_reason}")
        primary_name   = dd.rune_style_name(runes.primary_tree)
        secondary_name = dd.rune_style_name(runes.secondary_tree)
        print(f"▶ 符文天赋  （来源: {runes.source}）")
        print(f"  主系: {primary_name}  核心: {tr_rune(runes.keystone, dd)}")
        if runes.primary_perks:
            print(f"  主系符文: {tr_runes_list(runes.primary_perks, dd)}")
        print(f"  副系: {secondary_name}")
        if runes.secondary_perks:
            print(f"  副系符文: {tr_runes_list(runes.secondary_perks, dd)}")
    else:
        print("▶ 符文数据获取失败")

    print()


# ── 主流程 ───────────────────────────────────────────────────────────────────

def main() -> None:
    print("══════════════════════════════════════════════════════════")
    print("  verify_step4.py  —  第4步端到端验证")
    print("══════════════════════════════════════════════════════════\n")

    # 1. 加载 DDragon（英雄 + 装备 + 符文）
    dd = DDragonClient(cache_dir=CACHE_DIR)
    version = dd.ensure_loaded()
    print(f"\n[*] DDragon 版本 {version} 就绪\n")

    # 2. 读取 fixture + 解析
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ds  = parse_lcu_session(raw, dd)

    # 3. 推断敌方位置 + 匹配对位
    enrich_session(ds)

    # 4. 打印双方阵容
    print_teams(ds)

    # 5. 查询我方英雄出装 + 符文（带中文翻译）
    local = ds.local_member
    if not local or not local.champ.numeric_id:
        print("无法确定本地玩家，跳过出装查询")
        return

    print(f"\n查询出装: {local.champ.zh_name}（{local.champ.en_id}） / {local.position_cn}\n")
    provider = OpggProvider(tier="gold_plus")
    build = provider.get_build(local.champ.en_id, local.position)
    runes = provider.get_runes(local.champ.en_id, local.position)
    print_build(build, runes, dd)

    print("══════════════════════════════════════════════════════════")
    print("  验证完成")
    print("══════════════════════════════════════════════════════════")


if __name__ == "__main__":
    main()
