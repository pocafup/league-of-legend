"""
敌方阵容构成分析 — 为 Claude 生成"针对性出装调整"提供结构化摘要。

基于 DDragon tags 粗分类，辅以小型修正表。
输出是给 Claude 读的中文摘要字符串，不做最终判断。
"""
from __future__ import annotations

from dataclasses import dataclass

from data.ddragon import DDragonClient

# ── 修正表（DDragon tags 不够精确的情况）──────────────────────────────────────

# 主要输出为魔法伤害（尽管 tags 是 Fighter 或 Assassin）
_AP_OVERRIDE = {
    "Kennen", "Mordekaiser", "Garen",   # Garen 主要物理，但 Mordekaiser 全魔法
    "Singed", "Rumble", "Teemo",
    "Ekko", "Diana", "Kassadin",
    "Neeko", "Lillia", "Maokai",
    "Amumu", "Fiddlesticks",
}

# 混合伤害（两种都有）
_MIXED_OVERRIDE = {
    "Corki", "Kaisa", "Ezreal",
    "Jayce", "Gangplank",
}

# 拥有大量硬控的英雄（完全失控：眩晕/击飞/挤开/抓取）
_HARD_CC = {
    "Alistar", "Blitzcrank", "Leona", "Nautilus", "Thresh",
    "Morgana", "Lux", "Ashe", "Zac", "Jarvaniv", "Vi",
    "Poppy", "Rell", "Malphite", "Amumu", "Rammus",
    "Lissandra", "Sejuani", "Wukong", "Veigar", "Neeko",
    "Anivia", "Ryze", "Maokai", "Sett", "Volibear",
    "Nunu", "Nid", "Syndra", "Ahri", "Annie", "Kennen",
    "Darius", "Pantheon", "Riven", "Camille", "Irelia",
    "Garen", "Nasus", "Illaoi", "Urgot", "Aatrox",
    "Gragas", "Warwick", "Sion", "Cho'gath", "Chogath",
    "Hecarim", "Nocturne", "Shyvana", "Jax", "Trundle",
    "Fiora",  # 没有硬控，不算
    "Kled", "Gnar", "Xin", "XinZhao",
    "Braum", "Janna", "Nami", "Soraka",   # Soraka 没硬控
    "Ornn", "Galio", "Malzahar",
    "Brand", "Zilean",
}
_HARD_CC.discard("Soraka")   # 修正：Soraka 无硬控

# 拥有大量回复/护盾的英雄（决定是否需要斩铁）
_HEALING_SHIELD = {
    "Soraka", "Nami", "Lulu", "Sona", "Seraphine",
    "Aatrox", "Warwick", "Sylas", "Vladimir",
    "Yuumi", "Janna", "Karma", "Shen",
    "Bard",  # 有护盾
    "Renata",
}


@dataclass
class EnemyComp:
    ap_count:      int = 0   # 法术主要伤害来源数量
    ad_count:      int = 0   # 物理主要伤害来源数量
    tank_count:    int = 0   # 坦克数（高血线/厚甲）
    hard_cc_count: int = 0   # 有硬控的英雄数量
    healer_count:  int = 0   # 有大量回复/护盾的英雄数量
    names_ap:      list[str] = None
    names_ad:      list[str] = None
    names_tank:    list[str] = None
    names_cc:      list[str] = None
    names_healer:  list[str] = None

    def __post_init__(self):
        for f in ("names_ap", "names_ad", "names_tank", "names_cc", "names_healer"):
            if getattr(self, f) is None:
                setattr(self, f, [])


def analyze_enemy_comp(enemy_team, dd: DDragonClient) -> EnemyComp:
    """
    分析敌方五人阵容，返回 EnemyComp 摘要。
    enemy_team: list[TeamMember]（只分析 numeric_id != 0 的成员）
    """
    comp = EnemyComp()

    for m in enemy_team:
        if m.champ.numeric_id == 0:
            continue
        en_id = m.champ.en_id
        zh    = m.champ.zh_name
        tags  = dd.champ_tags(en_id)

        # ── 伤害类型 ──────────────────────────────────────────────────────────
        if en_id in _AP_OVERRIDE:
            comp.ap_count += 1
            comp.names_ap.append(zh)
        elif en_id in _MIXED_OVERRIDE:
            # 混合算半个 AD 半个 AP，不单独列，但加进两者
            comp.ap_count += 1
            comp.ad_count += 1
            comp.names_ap.append(f"{zh}(混)")
            comp.names_ad.append(f"{zh}(混)")
        elif "Mage" in tags:
            comp.ap_count += 1
            comp.names_ap.append(zh)
        elif "Marksman" in tags:
            comp.ad_count += 1
            comp.names_ad.append(zh)
        elif "Assassin" in tags:
            # 大多数刺客是物理；AP 刺客在 _AP_OVERRIDE 里已处理
            comp.ad_count += 1
            comp.names_ad.append(zh)
        elif "Fighter" in tags:
            comp.ad_count += 1
            comp.names_ad.append(zh)
        elif "Support" in tags:
            # 辅助伤害通常不是核心，但法系辅助要算
            pass
        elif "Tank" in tags:
            pass  # 坦克伤害通常次要

        # ── 坦克 ──────────────────────────────────────────────────────────────
        if "Tank" in tags or ("Fighter" in tags and "Tank" in tags):
            comp.tank_count += 1
            comp.names_tank.append(zh)

        # ── 硬控 ──────────────────────────────────────────────────────────────
        if en_id in _HARD_CC:
            comp.hard_cc_count += 1
            comp.names_cc.append(zh)

        # ── 回复/护盾 ─────────────────────────────────────────────────────────
        if en_id in _HEALING_SHIELD:
            comp.healer_count += 1
            comp.names_healer.append(zh)

    return comp


def comp_summary_for_claude(comp: EnemyComp) -> str:
    """把 EnemyComp 转成给 Claude 读的中文摘要。"""
    lines = ["敌方阵容构成分析："]

    total_dmg = comp.ap_count + comp.ad_count
    if comp.ap_count > comp.ad_count:
        lines.append(f"- 伤害构成：偏法系（{comp.ap_count} 个法系来源：{', '.join(comp.names_ap)}）")
    elif comp.ad_count > comp.ap_count:
        lines.append(f"- 伤害构成：偏物理（{comp.ad_count} 个物理来源：{', '.join(comp.names_ad)}）")
    else:
        lines.append(f"- 伤害构成：均衡（物理 {comp.ad_count} 个，法系 {comp.ap_count} 个）")

    if comp.tank_count >= 2:
        lines.append(f"- 厚重阵容：{comp.tank_count} 个坦克/重装（{', '.join(comp.names_tank)}）→ 考虑破甲/减甲")
    elif comp.tank_count == 1:
        lines.append(f"- 1 个坦克：{', '.join(comp.names_tank)}")

    if comp.hard_cc_count >= 3:
        lines.append(f"- 高控制：{comp.hard_cc_count} 个硬控来源（{', '.join(comp.names_cc)}）→ 考虑水银带或腱鞘")
    elif comp.hard_cc_count >= 1:
        lines.append(f"- 有硬控：{', '.join(comp.names_cc)}")

    if comp.healer_count >= 2:
        lines.append(f"- 高回复：{comp.healer_count} 个回复/护盾（{', '.join(comp.names_healer)}）→ 考虑斩铁相关装备")
    elif comp.healer_count == 1:
        lines.append(f"- 有回复：{', '.join(comp.names_healer)}")

    if total_dmg == 0:
        lines.append("- （伤害构成无法分析，可能主要是坦克/辅助）")

    return "\n".join(lines)
