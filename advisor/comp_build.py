"""
多对位出装混合 + 阵容打分合成引擎。

流程：
  1. 拉取 my vs 每个敌方英雄 的装备排名表（5张），样本不足的跳过。
  2. 按位置加权混合 item3-5 的综合分。
  3. 根据阵容档案（物理%/法术%/控制/坦克/回复）对装备打加权分。
  4. item1-2 保持单对位（对线对手）数据为主。
  5. 输出 CompBuildResult，每件装备带入选原因。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── 配置加载 ──────────────────────────────────────────────────────────────────

_CFG_PATH = Path(__file__).parent.parent / "config" / "comp_scoring.json"

_DEFAULT_CFG: dict = {
    "position_weights": {"lane": 0.35, "jungle": 0.20, "other": 0.15},
    "min_matchup_sample": 200,
    "slots_early": ["item1", "item2"],
    "slots_late":  ["item3", "item4", "item5"],
    "comp_rules": {
        "high_magic_threshold":    0.55,
        "high_physical_threshold": 0.55,
        "high_healer_threshold":   2,
        "high_cc_threshold":       3,
        "high_tank_threshold":     2,
        "base_boost": 0.25,
        "tag_boosts": {
            "MagicResist":      {"condition": "high_magic",  "boost": 0.25},
            "Armor":            {"condition": "high_phys",   "boost": 0.20},
            "Tenacity":         {"condition": "high_cc",     "boost": 0.25},
            "ArmorPenetration": {"condition": "high_tank",   "boost": 0.25},
            "MagicPenetration": {"condition": "high_tank",   "boost": 0.15},
        },
        "gw_item_ids": [3033, 3123, 3165, 6933, 3036, 6609],
        "gw_boost_condition": "high_healer",
        "gw_boost": 0.30,
    },
}


def _load_cfg() -> dict:
    try:
        return json.loads(_CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _DEFAULT_CFG


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass
class ScoredItem:
    item_id:         int
    composite_score: float
    data_score:      float
    comp_bonus:      float
    reasons:         list[str] = field(default_factory=list)


@dataclass
class CompBuildResult:
    starter:      list[int]          = field(default_factory=list)
    boots:        list[int]          = field(default_factory=list)
    early_items:  list[ScoredItem]   = field(default_factory=list)   # item1-2
    late_items:   list[ScoredItem]   = field(default_factory=list)   # item3-5 top picks
    comp_profile: dict               = field(default_factory=dict)
    sources:      list[str]          = field(default_factory=list)
    warnings:     list[str]          = field(default_factory=list)


# ── 主入口 ────────────────────────────────────────────────────────────────────

def build_comp_build(
    my_en_id:       str,
    my_lane:        str,
    enemy_refs:     list,          # list[ChampRef] — en_id + lane + zh_name
    provider,                      # LolalyticsProvider
    comp_profile:   dict,          # from EnemyComp.to_profile_dict()
    item_tags_fn,                  # dd.item_tags(item_id) -> list[str]
) -> CompBuildResult:
    """入口：拉5张对位表 → 混合 → 阵容打分 → 返回 CompBuildResult。"""
    cfg    = _load_cfg()
    pw     = cfg["position_weights"]
    rules  = cfg["comp_rules"]
    min_n  = cfg.get("min_matchup_sample", 200)

    # ── 1. 收集各对位的装备表 ─────────────────────────────────────────────────
    matchups: list[tuple[float, str, str, Optional[dict]]] = []  # (weight, en_id, zh_name, table)
    lane_table: Optional[dict] = None
    lane_en_id = ""

    for ref in enemy_refs:
        en_id   = ref.en_id   if hasattr(ref, "en_id")   else ref.get("en_id", "")
        lane    = ref.lane    if hasattr(ref, "lane")     else ref.get("lane", "")
        zh_name = ref.zh_name if hasattr(ref, "zh_name")  else ref.get("zh_name", en_id)
        if not en_id or en_id == "Unknown":
            continue

        if lane == my_lane:
            w = pw["lane"]
            is_lane = True
        elif lane == "jungle":
            w = pw["jungle"]
            is_lane = False
        else:
            w = pw["other"]
            is_lane = False

        table = provider.get_item_table(my_en_id, my_lane, en_id, min_n)
        matchups.append((w, en_id, zh_name, table))

        if is_lane and table:
            lane_table = table
            lane_en_id = en_id

    # 如果对线对手没有足够样本，取权重最高且有数据的
    if lane_table is None:
        valid = [(w, en_id, zh, t) for w, en_id, zh, t in matchups if t]
        if valid:
            lane_table = max(valid, key=lambda x: x[0])[3]

    sources:  list[str] = []
    warnings: list[str] = []
    for w, en_id, zh, t in matchups:
        if t:
            n = t.get("n", 0)
            sources.append(f"vs {zh}（权重{w:.0%}，{n:,}局）")
        else:
            warnings.append(f"vs {zh} 样本不足（< {min_n}），未纳入混合")

    # ── 2. 起手 + 鞋子（来自对线对手单对位数据）────────────────────────────
    starter: list[int] = []
    boots:   list[int] = []
    if lane_table:
        ss = lane_table.get("startSet", [])
        if ss and isinstance(ss[0], dict):
            starter = ss[0].get("ids", [])
        b_list = lane_table.get("boots", [])
        if b_list and isinstance(b_list[0], dict):
            boots = [b_list[0]["id"]]

    # ── 3. item1-2 (对线期，以对线对手数据为主) ──────────────────────────────
    early_items: list[ScoredItem] = []
    if lane_table:
        for slot in ("item1", "item2"):
            entries = lane_table.get(slot, [])
            if not entries:
                continue
            best = entries[0]
            lane_label = next((zh for w, eid, zh, t in matchups if t is lane_table), "对线对手")
            early_items.append(ScoredItem(
                item_id         = best["id"],
                composite_score = best["wr"] / 100,
                data_score      = best["wr"] / 100,
                comp_bonus      = 0.0,
                reasons         = [
                    f"对线对位最优（vs {lane_label}：{best['wr']:.1f}% WR，{best['pick']:.0f}% 皮克率）"
                ],
            ))

    # ── 4. item3-5 多对位混合 + 阵容打分 ────────────────────────────────────
    late_items = _mix_and_score(matchups, comp_profile, item_tags_fn, rules)

    # 去除与 early 重复的装备
    early_ids = {s.item_id for s in early_items}
    late_items = [s for s in late_items if s.item_id not in early_ids][:3]

    return CompBuildResult(
        starter      = starter,
        boots        = boots,
        early_items  = early_items,
        late_items   = late_items,
        comp_profile = comp_profile,
        sources      = sources,
        warnings     = warnings,
    )


# ── 内部：混合 + 打分 ─────────────────────────────────────────────────────────

def _mix_and_score(
    matchups:     list[tuple[float, str, str, Optional[dict]]],
    comp_profile: dict,
    item_tags_fn,
    rules:        dict,
) -> list[ScoredItem]:
    """
    把各对位 item3-5 的装备按权重混合，叠加阵容加分，返回排序列表。
    """
    # agg[item_id] = [(weight, wr, pick, zh_name), ...]
    agg: dict[int, list[tuple[float, float, float, str]]] = {}

    for w, en_id, zh_name, table in matchups:
        if not table:
            continue
        for slot in ("item3", "item4", "item5"):
            for entry in table.get(slot, [])[:12]:  # 每槽最多取前12
                iid  = entry["id"]
                wr   = entry["wr"]    # 0-100
                pick = entry["pick"]  # 0-100
                if iid not in agg:
                    agg[iid] = []
                agg[iid].append((w, wr, pick, zh_name))

    scored: list[ScoredItem] = []
    for iid, contrib_list in agg.items():
        # 数据分：Σ weight × (wr/100) × (pick/100)
        data_score = sum(w * (wr / 100) * (pick / 100) for w, wr, pick, _ in contrib_list)

        # 阵容附加分
        comp_bonus, comp_reasons = _comp_bonus(iid, comp_profile, item_tags_fn, rules)

        # 生成理由字符串
        detail_parts = [
            f"vs {zh}:{wr:.1f}%WR×{pk:.0f}%皮克(权{w:.0%})"
            for w, wr, pk, zh in sorted(contrib_list, key=lambda x: -x[0])[:3]
        ]
        reasons = [f"数据分 {data_score:.4f}（{'、'.join(detail_parts)}）"]
        reasons.extend(comp_reasons)

        scored.append(ScoredItem(
            item_id         = iid,
            composite_score = data_score + comp_bonus,
            data_score      = data_score,
            comp_bonus      = comp_bonus,
            reasons         = reasons,
        ))

    scored.sort(key=lambda x: x.composite_score, reverse=True)
    return scored


def _comp_bonus(
    iid:          int,
    comp_profile: dict,
    item_tags_fn,
    rules:        dict,
) -> tuple[float, list[str]]:
    """根据阵容档案给单件装备计算加分和理由。"""
    tags      = set(item_tags_fn(iid))
    bonus     = 0.0
    reasons:  list[str] = []
    tag_cfg   = rules.get("tag_boosts", {})

    phys_pct      = comp_profile.get("phys_pct", 0.5)
    magic_pct     = comp_profile.get("magic_pct", 0.5)
    healer_count  = comp_profile.get("healer_count", 0)
    cc_count      = comp_profile.get("hard_cc_count", 0)
    tank_count    = comp_profile.get("tank_count", 0)

    cond_map = {
        "high_magic":  magic_pct  >= rules.get("high_magic_threshold",    0.55),
        "high_phys":   phys_pct   >= rules.get("high_physical_threshold", 0.55),
        "high_cc":     cc_count   >= rules.get("high_cc_threshold",       3),
        "high_tank":   tank_count >= rules.get("high_tank_threshold",     2),
        "high_healer": healer_count >= rules.get("high_healer_threshold", 2),
    }

    for tag, cfg in tag_cfg.items():
        if tag not in tags:
            continue
        cond = cfg.get("condition", "")
        if not cond_map.get(cond, False):
            continue
        b = cfg.get("boost", 0.0)
        bonus += b
        reason = _boost_reason(tag, b, comp_profile, cond)
        reasons.append(reason)

    # 斩铁单独处理（用 item ID 列表）
    gw_ids  = set(rules.get("gw_item_ids", []))
    gw_cond = rules.get("gw_boost_condition", "high_healer")
    if iid in gw_ids and cond_map.get(gw_cond, False):
        b = rules.get("gw_boost", 0.30)
        bonus += b
        healer_names = comp_profile.get("healer_names", [])
        reasons.append(
            f"阵容加权 +{b:.0%}：对面含高回复（{'、'.join(healer_names[:2])}），斩铁提权"
        )

    return bonus, reasons


def _boost_reason(tag: str, boost: float, profile: dict, cond: str) -> str:
    label_map = {
        "high_magic":  f"法系阵容（魔法伤害占{profile.get('magic_pct', 0):.0%}，AP来源：{'、'.join(profile.get('ap_names', [])[:2])}）",
        "high_phys":   f"物理阵容（物理伤害占{profile.get('phys_pct', 0):.0%}，AD来源：{'、'.join(profile.get('ad_names', [])[:2])}）",
        "high_cc":     f"高控制阵容（{profile.get('hard_cc_count', 0)}个硬控：{'、'.join(profile.get('cc_names', [])[:2])}）",
        "high_tank":   f"多坦阵容（{profile.get('tank_count', 0)}个坦克：{'、'.join(profile.get('tank_names', [])[:2])}）",
        "high_healer": f"高回复阵容（{profile.get('healer_count', 0)}个回复来源）",
    }
    tag_cn = {"MagicResist": "魔抗装", "Armor": "护甲装", "Tenacity": "韧性装",
              "ArmorPenetration": "破甲装", "MagicPenetration": "穿魔装"}.get(tag, tag)
    return f"阵容加权 +{boost:.0%}：{label_map.get(cond, cond)}，{tag_cn}提权"
