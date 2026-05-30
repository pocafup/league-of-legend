"""
敌方位置推断：静态英雄→位置映射 + 贪心冲突解决。

算法：
1. 对每个敌方英雄查 CHAMPION_ROLES，得到有序偏好列表。
2. 按"偏好列表最短（最专一）→最长（最灵活）"排序，依次分配最优空位。
3. 若所有偏好均被占用，从剩余空位中取一个（最后兜底）。
4. 每个分配结果标注置信度（高频/次选/兜底）。
"""
from __future__ import annotations

from dataclasses import dataclass

POSITIONS = ["top", "jungle", "middle", "bottom", "utility"]

# DDragon en_id（小写）→ 偏好位置列表（由高频到低频）
# 覆盖 patch 16.11 所有 177 个英雄
CHAMPION_ROLES: dict[str, list[str]] = {
    # A
    "aatrox":       ["top"],
    "ahri":         ["middle"],
    "akali":        ["middle", "top"],
    "akshan":       ["middle", "top"],
    "alistar":      ["utility"],
    "ambessa":      ["top", "jungle"],
    "amumu":        ["jungle", "utility"],
    "anivia":       ["middle"],
    "annie":        ["middle", "utility"],
    "aphelios":     ["bottom"],
    "ashe":         ["bottom", "utility"],
    "aurelionsol":  ["middle"],
    "aurora":       ["middle", "top"],
    "azir":         ["middle"],
    # B
    "bard":         ["utility"],
    "belveth":      ["jungle"],
    "blitzcrank":   ["utility"],
    "brand":        ["utility", "middle"],
    "braum":        ["utility"],
    "briar":        ["jungle", "top"],
    # C
    "caitlyn":      ["bottom"],
    "camille":      ["top"],
    "cassiopeia":   ["middle", "top"],
    "chogath":      ["top", "middle"],
    "corki":        ["middle", "bottom"],
    # D
    "darius":       ["top"],
    "diana":        ["jungle", "middle"],
    "drmundo":      ["top", "jungle"],
    "draven":       ["bottom"],
    # E
    "ekko":         ["jungle", "middle"],
    "elise":        ["jungle"],
    "evelynn":      ["jungle"],
    "ezreal":       ["bottom", "middle"],
    # F
    "fiddlesticks": ["jungle", "utility"],
    "fiora":        ["top"],
    "fizz":         ["middle"],
    # G
    "galio":        ["middle", "utility"],
    "gangplank":    ["top"],
    "garen":        ["top"],
    "gnar":         ["top"],
    "gragas":       ["jungle", "utility", "top"],
    "graves":       ["jungle"],
    "gwen":         ["top", "jungle"],
    # H
    "hecarim":      ["jungle"],
    "heimerdinger": ["middle", "top", "utility"],
    "hwei":         ["middle", "utility"],
    # I
    "illaoi":       ["top"],
    "irelia":       ["top", "middle"],
    "ivern":        ["jungle"],
    # J
    "janna":        ["utility"],
    "jarvaniv":     ["jungle"],
    "jax":          ["top", "jungle"],
    "jayce":        ["top", "middle"],
    "jhin":         ["bottom"],
    "jinx":         ["bottom"],
    # K
    "ksante":       ["top"],
    "kaisa":        ["bottom"],
    "kalista":      ["bottom"],
    "karma":        ["utility", "middle", "top"],
    "karthus":      ["jungle", "middle", "bottom"],
    "kassadin":     ["middle"],
    "katarina":     ["middle"],
    "kayle":        ["top", "middle"],
    "kayn":         ["jungle"],
    "kennen":       ["top"],
    "khazix":       ["jungle"],
    "kindred":      ["jungle"],
    "kled":         ["top"],
    "kogmaw":       ["bottom"],
    # L
    "leblanc":      ["middle"],
    "leesin":       ["jungle"],
    "leona":        ["utility"],
    "lillia":       ["jungle"],
    "lissandra":    ["middle"],
    "lucian":       ["bottom", "middle"],
    "lulu":         ["utility"],
    "lux":          ["utility", "middle"],
    # M
    "malphite":     ["top", "utility"],
    "malzahar":     ["middle"],
    "maokai":       ["utility", "top", "jungle"],
    "masteryi":     ["jungle"],
    "mel":          ["middle"],
    "milio":        ["utility"],
    "missfortune":  ["bottom"],
    "monkeyking":   ["jungle", "top"],
    "mordekaiser":  ["top"],
    "morgana":      ["utility", "middle"],
    # N
    "naafiri":      ["middle"],
    "nami":         ["utility"],
    "nasus":        ["top"],
    "nautilus":     ["utility"],
    "neeko":        ["middle", "utility"],
    "nidalee":      ["jungle"],
    "nilah":        ["bottom"],
    "nocturne":     ["jungle", "top"],
    "nunu":         ["jungle"],
    # O
    "olaf":         ["jungle", "top"],
    "orianna":      ["middle"],
    "ornn":         ["top"],
    # P
    "pantheon":     ["utility", "top", "middle"],
    "poppy":        ["top", "jungle"],
    "pyke":         ["utility"],
    # Q
    "qiyana":       ["middle", "jungle"],
    "quinn":        ["top"],
    # R
    "rakan":        ["utility"],
    "rammus":       ["jungle"],
    "reksai":       ["jungle"],
    "rell":         ["utility"],
    "renata":       ["utility"],
    "renekton":     ["top"],
    "rengar":       ["jungle", "top"],
    "riven":        ["top"],
    "rumble":       ["top", "jungle"],
    "ryze":         ["middle", "top"],
    # S
    "samira":       ["bottom"],
    "sejuani":      ["jungle"],
    "senna":        ["utility", "bottom"],
    "seraphine":    ["utility", "middle"],
    "sett":         ["top", "utility"],
    "shaco":        ["jungle", "utility"],
    "shen":         ["top"],
    "shyvana":      ["jungle"],
    "singed":       ["top"],
    "sion":         ["top"],
    "sivir":        ["bottom"],
    "skarner":      ["jungle"],
    "smolder":      ["bottom"],
    "sona":         ["utility"],
    "soraka":       ["utility"],
    "swain":        ["utility", "middle", "top"],
    "sylas":        ["middle", "jungle"],
    "syndra":       ["middle"],
    # T
    "tahmkench":    ["utility", "top"],
    "taliyah":      ["jungle", "middle"],
    "talon":        ["middle", "jungle"],
    "taric":        ["utility"],
    "teemo":        ["top"],
    "thresh":       ["utility"],
    "tristana":     ["bottom"],
    "trundle":      ["jungle", "top"],
    "tryndamere":   ["top"],
    "twistedfate":  ["middle"],
    "twitch":       ["bottom", "jungle"],
    # U
    "udyr":         ["jungle", "top"],
    "urgot":        ["top"],
    # V
    "varus":        ["bottom", "utility"],
    "vayne":        ["bottom", "top"],
    "veigar":       ["middle", "utility"],
    "velkoz":       ["utility", "middle"],
    "vex":          ["middle"],
    "vi":           ["jungle"],
    "viego":        ["jungle"],
    "viktor":       ["middle"],
    "vladimir":     ["middle", "top"],
    "volibear":     ["jungle", "top"],
    # W
    "warwick":      ["jungle", "top"],
    # X
    "xayah":        ["bottom"],
    "xerath":       ["utility", "middle"],
    "xinzhao":      ["jungle"],
    # Y
    "yasuo":        ["middle", "top"],
    "yone":         ["middle", "top"],
    "yorick":       ["top"],
    "yunara":       ["utility"],
    "yuumi":        ["utility"],
    # Z
    "zaahen":       ["jungle"],
    "zac":          ["jungle"],
    "zed":          ["middle"],
    "zeri":         ["bottom"],
    "ziggs":        ["bottom", "middle"],
    "zilean":       ["utility", "middle"],
    "zoe":          ["middle"],
    "zyra":         ["utility", "middle"],
}


@dataclass
class PositionGuess:
    position: str       # "top" / "jungle" / "middle" / "bottom" / "utility"
    confidence: str     # "很可能" / "可能" / "不确定"
    source: str         # 推断来源说明（用于输出"可能是"标注）


def _greedy_assign(
    champ_prefs: dict[int, list[str]],
) -> dict[int, tuple[str, int]]:
    """
    贪心最优匹配。

    先分配"最专一"的英雄（偏好列表最短），减少后续冲突。
    返回 cell_id → (assigned_position, priority_index)
      priority_index 0 = 主位置，1 = 次选，... 99 = 兜底
    """
    available: set[str] = set(POSITIONS)
    result: dict[int, tuple[str, int]] = {}

    # 从最专一（选项最少）到最灵活排序
    ordered = sorted(champ_prefs.items(), key=lambda kv: len(kv[1]))

    for cell_id, prefs in ordered:
        assigned = False
        for priority, pos in enumerate(prefs):
            if pos in available:
                available.discard(pos)
                result[cell_id] = (pos, priority)
                assigned = True
                break
        if not assigned and available:
            fallback = next(iter(available))
            available.discard(fallback)
            result[cell_id] = (fallback, 99)

    return result


def infer_enemy_positions(enemy_team: list) -> dict[int, PositionGuess]:
    """
    对 enemy_team（list[TeamMember]）中每个英雄推断位置。
    返回 cell_id → PositionGuess。
    """
    # 构建 cell_id → 偏好位置列表
    champ_prefs: dict[int, list[str]] = {}
    for m in enemy_team:
        if m.champ.numeric_id == 0:
            champ_prefs[m.cell_id] = list(POSITIONS)
            continue
        key = m.champ.en_id.lower()
        prefs = CHAMPION_ROLES.get(key)
        if not prefs:
            print(f"  [推断] 未知英雄 {m.champ.en_id}，使用全部位置作为候选")
            prefs = list(POSITIONS)
        champ_prefs[m.cell_id] = prefs

    assignment = _greedy_assign(champ_prefs)

    guesses: dict[int, PositionGuess] = {}
    for m in enemy_team:
        pos, priority = assignment.get(m.cell_id, ("top", 99))
        if priority == 0:
            confidence = "很可能"
            source = f"{m.champ.zh_name} 最常出现在此路"
        elif priority == 1:
            confidence = "可能"
            source = f"{m.champ.zh_name} 次选位置（主位置被占）"
        elif priority == 99:
            confidence = "不确定"
            source = "所有偏好位置均被占，随机分配"
        else:
            confidence = "可能"
            source = f"{m.champ.zh_name} 第{priority + 1}选位置"
        guesses[m.cell_id] = PositionGuess(position=pos, confidence=confidence, source=source)

    return guesses
