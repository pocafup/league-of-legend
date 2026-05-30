"""
把 stats 数据 + DDragon 翻译 + Claude tips 汇成最终 Advice 对象。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from advisor.comp_adjust import EnemyComp, analyze_enemy_comp, comp_summary_for_claude
from advisor.session import DraftSession
from advisor.tips import generate_tips
from data.ddragon import DDragonClient
from stats.provider import Build, Matchup, Runes, StatsProvider


@dataclass
class Advice:
    ds: DraftSession
    version: str
    queue_cn: str          # "排位赛" / "快速对战" 等
    build: Optional[Build]
    runes: Optional[Runes]
    matchup: Optional[Matchup]
    enemy_comp: Optional[EnemyComp] = None
    lane_tips:   list[str] = field(default_factory=list)
    teamfight:   list[str] = field(default_factory=list)
    comp_adjust: list[str] = field(default_factory=list)
    tips_error: str = ""   # 非空 = tips 生成失败原因


# queueId → 中文名（只列常见值，其余显示"匹配对局"）
_QUEUE_NAMES: dict[int, str] = {
    420: "排位赛",
    440: "灵活排位",
    400: "正常（草稿）",
    430: "正常（盲选）",
    450: "大乱斗",
    900: "无限乱斗",
    1020: "一命模式",
    0: "自定义",
}


def _fmt_items(ids: list[int], dd: DDragonClient) -> str:
    return " → ".join(dd.item_name(i) for i in ids) if ids else "（无）"


def _fmt_runes_list(ids: list[int], dd: DDragonClient) -> str:
    return " / ".join(dd.rune_name(i) for i in ids) if ids else "（无）"


def _build_build_desc(build: Optional[Build], dd: DDragonClient) -> str:
    if not build:
        return "出装：暂无数据"
    parts = []
    if build.starter:
        parts.append(f"起手：{_fmt_items(build.starter, dd)}")
    if build.boots:
        parts.append(f"鞋子：{_fmt_items(build.boots, dd)}")
    if build.core:
        parts.append(f"核心：{_fmt_items(build.core, dd)}")
    # 按需 = 四/五/六件合并
    situational = build.fourth + build.fifth + build.sixth
    if situational:
        parts.append(f"按需：{' / '.join(dd.item_name(i) for i in situational)}")
    return "；".join(parts)


def _build_runes_desc(runes: Optional[Runes], dd: DDragonClient) -> str:
    if not runes:
        return "符文：暂无数据"
    primary  = dd.rune_style_name(runes.primary_tree)
    keystone = dd.rune_name(runes.keystone)
    pp       = _fmt_runes_list(runes.primary_perks, dd)
    secondary = dd.rune_style_name(runes.secondary_tree)
    sp        = _fmt_runes_list(runes.secondary_perks, dd)
    return f"符文：主系{primary}（{keystone}），{pp}；副系{secondary}，{sp}"


def assemble_advice(
    ds: DraftSession,
    provider: StatsProvider,
    dd: DDragonClient,
    version: str,
) -> Advice:
    """
    完整流水线：获取出装/符文 → 生成 tips → 组装 Advice。
    每一步都容错，失败后用 None / 占位文本继续。
    """
    queue_cn = _QUEUE_NAMES.get(ds.queue_id, "匹配对局")

    local = ds.local_member
    build: Optional[Build]   = None
    runes: Optional[Runes]   = None
    matchup: Optional[Matchup] = None

    enemy_id = local.opponent.champ.en_id if (local and local.opponent) else ""

    if local and local.champ.numeric_id:
        try:
            build = provider.get_build(local.champ.en_id, local.position, enemy_id)
        except Exception as e:
            print(f"  [assemble] get_build 失败: {e}")
        try:
            runes = provider.get_runes(local.champ.en_id, local.position, enemy_id)
        except Exception as e:
            print(f"  [assemble] get_runes 失败: {e}")
        if local.opponent:
            try:
                matchup = provider.get_matchup(
                    local.champ.en_id, local.position, local.opponent.champ.en_id
                )
            except Exception as e:
                print(f"  [assemble] get_matchup 失败: {e}")

    # 阵容伤害/构成分析
    enemy_comp = analyze_enemy_comp(ds.enemy_team, dd)
    comp_sum   = comp_summary_for_claude(enemy_comp)

    # 构建给 tips 用的描述字符串（翻译后中文）
    build_desc = _build_build_desc(build, dd)
    runes_desc = _build_runes_desc(runes, dd)

    # 团队列表（包含英文 ID，避免中文名歧义，如"德玛西亚之翼" vs "德玛西亚之力"）
    my_team_list = [
        (m.champ.zh_name, m.champ.en_id, m.position_cn)
        for m in ds.my_team if m.champ.numeric_id
    ]
    enemy_team_list = [
        (m.champ.zh_name, m.champ.en_id, m.position_cn)
        for m in ds.enemy_team if m.champ.numeric_id
    ]

    opp_zh    = local.opponent.champ.zh_name if (local and local.opponent) else None
    opp_en    = local.opponent.champ.en_id   if (local and local.opponent) else ""
    opp_conf  = local.opponent.pos_confidence if (local and local.opponent) else ""
    my_pos_cn = local.position_cn if local else ""
    my_zh     = local.champ.zh_name if local else ""
    my_en     = local.champ.en_id   if local else ""
    wr        = matchup.win_rate if matchup else None

    print("  [assemble] 调用 Claude 生成建议…")
    lane_tips, teamfight, comp_adjust, tips_error = generate_tips(
        my_champ_zh=my_zh,
        my_champ_en=my_en,
        my_position_cn=my_pos_cn,
        opponent_zh=opp_zh,
        opponent_en=opp_en,
        opp_confidence=opp_conf,
        my_team=my_team_list,
        enemy_team=enemy_team_list,
        build_desc=build_desc,
        runes_desc=runes_desc,
        matchup_wr=wr,
        comp_summary=comp_sum,
    )

    return Advice(
        ds=ds,
        version=version,
        queue_cn=queue_cn,
        build=build,
        runes=runes,
        matchup=matchup,
        enemy_comp=enemy_comp,
        lane_tips=lane_tips,
        teamfight=teamfight,
        comp_adjust=comp_adjust,
        tips_error=tips_error,
    )
