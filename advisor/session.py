"""
将 LCU 原始 session dict 解析为统一内部结构。
两种输入来源（live / replay）共用同一解析逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from data.ddragon import ChampionInfo, DDragonClient

if TYPE_CHECKING:
    pass

POSITION_CN = {
    "top":     "上单",
    "jungle":  "打野",
    "middle":  "中单",
    "bottom":  "射手",
    "utility": "辅助",
}


@dataclass
class TeamMember:
    cell_id: int
    champ: ChampionInfo
    position: str    # 真实分路（LCU assignedPosition；敌方为空字符串）
    is_local: bool = False
    # 第4步：推断分路（敌方用）
    inferred_pos: str = ""
    pos_confidence: str = ""   # "很可能" / "可能" / "不确定"
    pos_source: str = ""       # 推断来源说明
    # 对线对手（由 enrich_session 填充）
    opponent: Optional["TeamMember"] = field(default=None, repr=False)

    @property
    def effective_position(self) -> str:
        """真实分路优先，其次推断分路。"""
        return self.position or self.inferred_pos

    @property
    def position_cn(self) -> str:
        return POSITION_CN.get(self.effective_position, self.effective_position or "未知")


@dataclass
class DraftSession:
    phase: str
    is_custom: bool
    local_cell_id: int
    queue_id: int = 0
    my_team: list[TeamMember] = field(default_factory=list)
    enemy_team: list[TeamMember] = field(default_factory=list)

    @property
    def local_member(self) -> TeamMember | None:
        return next((m for m in self.my_team if m.is_local), None)


def parse_lcu_session(raw: dict, dd: DDragonClient) -> DraftSession:
    """将 LCU /lol-champ-select/v1/session 返回值解析为 DraftSession。"""
    timer = raw.get("timer", {})
    phase = timer.get("phase", "")
    local_cell = raw.get("localPlayerCellId", -1)

    # 从 actions 补充 championId（机器人/某些边缘情况下 theirTeam 字段为 0）
    picks_from_actions: dict[int, int] = {}
    for phase_actions in raw.get("actions", []):
        for act in phase_actions:
            if act.get("type") == "pick" and act.get("completed") and act.get("championId", 0):
                picks_from_actions[act["actorCellId"]] = act["championId"]

    def make_member(entry: dict) -> TeamMember:
        cell_id = entry.get("cellId", -1)
        cid = entry.get("championId", 0) or picks_from_actions.get(cell_id, 0)
        return TeamMember(
            cell_id=cell_id,
            champ=dd.get_or_unknown(cid),
            position=entry.get("assignedPosition", ""),
            is_local=(cell_id == local_cell),
        )

    return DraftSession(
        phase=phase,
        is_custom=raw.get("isCustomGame", False),
        local_cell_id=local_cell,
        queue_id=raw.get("queueId", 0),
        my_team=[make_member(e) for e in raw.get("myTeam", [])],
        enemy_team=[make_member(e) for e in raw.get("theirTeam", [])],
    )


def enrich_session(ds: DraftSession) -> None:
    """
    原地更新 DraftSession：
    1. 对敌方每个英雄推断分路（贪心分配，无冲突）。
    2. 为我方每个成员找对线对手（my.position == enemy.inferred_pos）。
    """
    from advisor.role_inference import infer_enemy_positions

    guesses = infer_enemy_positions(ds.enemy_team)

    # 填充敌方推断位置
    for m in ds.enemy_team:
        guess = guesses.get(m.cell_id)
        if guess:
            m.inferred_pos   = guess.position
            m.pos_confidence = guess.confidence
            m.pos_source     = guess.source

    # 建立 position → 敌方成员映射
    enemy_by_pos: dict[str, TeamMember] = {}
    for m in ds.enemy_team:
        if m.inferred_pos:
            enemy_by_pos[m.inferred_pos] = m

    # 为我方成员找对位对手
    for m in ds.my_team:
        m.opponent = enemy_by_pos.get(m.position)
