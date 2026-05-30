"""
终端排版输出：按 CLAUDE.md 模板严格排版。
"""
from __future__ import annotations

import textwrap
from typing import Optional

from advisor.assemble import Advice
from data.ddragon import DDragonClient
from stats.provider import Build, Matchup, Runes

_W  = 54          # 分隔线宽度
_SEP  = "═" * _W
_LINE = "─" * _W


def _fmt_items(ids: list[int], dd: DDragonClient) -> str:
    return " → ".join(dd.item_name(i) for i in ids) if ids else "（无）"


def _fmt_rune_list(ids: list[int], dd: DDragonClient) -> str:
    return " / ".join(dd.rune_name(i) for i in ids) if ids else "（无）"


def _wr_label(wr: float) -> str:
    """把胜率转成 小劣/均势/小优/大优 等标签。"""
    if wr >= 54:   return "大优"
    if wr >= 52:   return "小优"
    if wr >= 48:   return "均势"
    if wr >= 46:   return "小劣"
    return "大劣"


def _wrap_numbered(items: list[str], indent: str = "  ") -> str:
    lines = []
    for i, tip in enumerate(items, 1):
        wrapped = textwrap.fill(tip, width=50, subsequent_indent=indent + "   ")
        lines.append(f"{indent}{i}. {wrapped[len(indent):]}")
    return "\n".join(lines)


def render_advice(advice: Advice, dd: DDragonClient) -> None:
    ds    = advice.ds
    local = ds.local_member
    build = advice.build
    runes = advice.runes
    matchup = advice.matchup

    # ── 标题栏 ───────────────────────────────────────────────
    print()
    print(_SEP)
    stale_note = ""
    if (build and build.stale) or (runes and runes.stale):
        stale_note = "  ⚠ 数据来自缓存"
    print(f" 选人完成 ✓   补丁: {advice.version}   {advice.queue_cn}{stale_note}")
    print(_SEP)

    if not local or not local.champ.numeric_id:
        print(" （无法确定本地玩家）")
        print(_LINE)
        return

    print(f" 你的位置: {local.position_cn:<4}  你的英雄: {local.champ.zh_name}（{local.champ.en_id}）")

    # ── 对线对手 + 胜率 ──────────────────────────────────────
    print()
    opp = local.opponent
    if opp:
        conf_word = "可能是" if opp.pos_confidence != "很可能" else ""
        opp_label = f"{conf_word} {opp.champ.zh_name}".strip()
        print(f"▶ 对线对手: {opp_label}   [{opp.pos_confidence} · {opp.pos_source}]")
    else:
        print("▶ 对线对手: 未知（位置推断失败）")

    if matchup and matchup.win_rate:
        wr = matchup.win_rate
        label = _wr_label(wr)
        sample = f"{matchup.sample_size:,}" if matchup.sample_size else "N/A"
        stale_m = f"  ⚠ 数据过期（{matchup.source}）" if matchup.stale else f"  来源: {matchup.source}"
        print(f"  对位胜率: {local.champ.zh_name} {wr:.1f}%  （{label} · 样本 {sample} 局）{stale_m}")
    else:
        print("  对位胜率: 暂无数据")

    # ── 推荐出装 ─────────────────────────────────────────────
    print()
    if build:
        src = build.source
        stale_b = f"  ⚠ 可能过期（{build.stale_reason}）" if build.stale else ""
        print(f"▶ 推荐出装  （{src}{stale_b}）")
        print(f"  起手 : {_fmt_items(build.starter, dd)}")
        print(f"  核心 : {_fmt_items(build.core, dd)}")
        situational = build.fourth + build.fifth + build.sixth
        if situational:
            seen_sit: list[int] = []
            for i in situational:
                if i not in seen_sit:
                    seen_sit.append(i)
            print(f"  按需 : {' / '.join(dd.item_name(i) for i in seen_sit)}")
        print(f"  鞋子 : {_fmt_items(build.boots, dd)}")
    else:
        print("▶ 推荐出装  （数据获取失败）")

    # ── 符文天赋 ─────────────────────────────────────────────
    print()
    if runes:
        src_r = runes.source
        stale_r = f"  ⚠ 可能过期" if runes.stale else ""
        print(f"▶ 符文天赋  （来源: {src_r}{stale_r}）")
        primary_name   = dd.rune_style_name(runes.primary_tree)
        secondary_name = dd.rune_style_name(runes.secondary_tree)
        keystone_name  = dd.rune_name(runes.keystone) if runes.keystone else "（无）"
        pp = _fmt_rune_list(runes.primary_perks, dd)
        sp = _fmt_rune_list(runes.secondary_perks, dd)
        print(f"  主系 : {primary_name} — {keystone_name} / {pp}")
        print(f"  副系 : {secondary_name} — {sp}")
        if runes.stat_shards:
            print(f"  碎片 : {_fmt_rune_list(runes.stat_shards, dd)}")
        else:
            print("  碎片 : （暂无）")
    else:
        print("▶ 符文天赋  （数据获取失败）")

    # ── 针对敌方阵容的调整 ───────────────────────────────────
    print()
    print("▶ 针对敌方阵容的调整")
    if advice.tips_error:
        print(f"  ⚠ {advice.tips_error}")
    elif not advice.comp_adjust:
        print("  （无需特殊调整）")
    else:
        for i, tip in enumerate(advice.comp_adjust, 1):
            lines = textwrap.wrap(tip, width=48)
            print(f"  {i}. {lines[0]}")
            for extra in lines[1:]:
                print(f"     {extra}")

    # ── 对线注意点 ───────────────────────────────────────────
    print()
    print("▶ 对线注意点")
    if advice.tips_error:
        print(f"  ⚠ {advice.tips_error}")
    else:
        for i, tip in enumerate(advice.lane_tips, 1):
            lines = textwrap.wrap(tip, width=48)
            print(f"  {i}. {lines[0]}")
            for extra in lines[1:]:
                print(f"     {extra}")

    # ── 团战分析 ─────────────────────────────────────────────
    print()
    print("▶ 团战分析")
    if advice.tips_error:
        print(f"  ⚠ {advice.tips_error}")
    else:
        for i, tip in enumerate(advice.teamfight, 1):
            lines = textwrap.wrap(tip, width=48)
            print(f"  {i}. {lines[0]}")
            for extra in lines[1:]:
                print(f"     {extra}")

    # ── 底部操作提示 ─────────────────────────────────────────
    print()
    print(_LINE)
    print(" (输入 r + Enter 重新查询 · Ctrl+C 退出)")
    print(_LINE)


def render_matchup(
    me_zh: str, me_en: str,
    vs_zh: str, vs_en: str,
    lane_cn: str, tier: str, version: str,
    matchup: Optional[Matchup],
    build: Optional[Build],
    runes: Optional[Runes],
    lane_tips: list[str],
    tips_error: str,
    dd: DDragonClient,
) -> None:
    tier_disp = tier.replace("_", " ").title().replace("Plus", "+")

    print()
    print(_SEP)
    print(f" 对位查询: {me_zh}({me_en}) vs {vs_zh}({vs_en}) / {lane_cn}")
    print(f" 版本: {version}   段位: {tier_disp}")
    print(_SEP)

    if matchup and matchup.win_rate:
        wr = matchup.win_rate
        label = _wr_label(wr)
        sample = f"{matchup.sample_size:,}" if matchup.sample_size else "N/A"
        stale_m = f"  ⚠ 数据过期" if matchup.stale else f"  来源: {matchup.source}"
        print(f" 对位胜率: {me_zh} {wr:.1f}%  （{label} · 样本 {sample} 局）{stale_m}")
    else:
        print(" 对位胜率: 暂无数据（冷门对位或样本不足）")

    print()
    if build:
        src = build.source
        stale_b = f"  ⚠ 可能过期（{build.stale_reason}）" if build.stale else ""
        print(f"▶ 推荐出装  （{src}{stale_b}）")
        print(f"  起手 : {_fmt_items(build.starter, dd)}")
        print(f"  核心 : {_fmt_items(build.core, dd)}")
        situational = build.fourth + build.fifth + build.sixth
        if situational:
            seen_sit: list[int] = []
            for i in situational:
                if i not in seen_sit:
                    seen_sit.append(i)
            print(f"  按需 : {' / '.join(dd.item_name(i) for i in seen_sit)}")
        print(f"  鞋子 : {_fmt_items(build.boots, dd)}")
    else:
        print("▶ 推荐出装  （数据获取失败）")

    print()
    if runes:
        src_r = runes.source
        stale_r = "  ⚠ 可能过期" if runes.stale else ""
        print(f"▶ 符文天赋  （来源: {src_r}{stale_r}）")
        primary_name   = dd.rune_style_name(runes.primary_tree)
        secondary_name = dd.rune_style_name(runes.secondary_tree)
        keystone_name  = dd.rune_name(runes.keystone) if runes.keystone else "（无）"
        pp = _fmt_rune_list(runes.primary_perks, dd)
        sp = _fmt_rune_list(runes.secondary_perks, dd)
        print(f"  主系 : {primary_name} — {keystone_name} / {pp}")
        print(f"  副系 : {secondary_name} — {sp}")
        if runes.stat_shards:
            print(f"  碎片 : {_fmt_rune_list(runes.stat_shards, dd)}")
    else:
        print("▶ 符文天赋  （数据获取失败）")

    print()
    print("▶ 核心对线思路")
    if tips_error:
        print(f"  ⚠ {tips_error}")
    else:
        for i, tip in enumerate(lane_tips, 1):
            lines = textwrap.wrap(tip, width=48)
            print(f"  {i}. {lines[0]}")
            for extra in lines[1:]:
                print(f"     {extra}")

    print()
    print(_LINE)
