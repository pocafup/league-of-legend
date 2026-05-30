"""
Abstract stats provider interface + shared dataclasses.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Build:
    starter: list[int] = field(default_factory=list)   # item IDs
    boots:   list[int] = field(default_factory=list)
    core:    list[int] = field(default_factory=list)
    fourth:  list[int] = field(default_factory=list)
    fifth:   list[int] = field(default_factory=list)
    sixth:   list[int] = field(default_factory=list)
    stale:   bool = False
    stale_reason: str = ""
    source:  str = ""


@dataclass
class Runes:
    primary_tree:    int = 0   # perkStyle ID, e.g. 8200
    secondary_tree:  int = 0   # perkStyle ID, e.g. 8000
    keystone:        int = 0   # perk ID, e.g. 8229
    primary_perks:   list[int] = field(default_factory=list)   # rows 2-4 of primary tree
    secondary_perks: list[int] = field(default_factory=list)   # 2 perks from secondary tree
    stat_shards:     list[int] = field(default_factory=list)   # 3 stat shards (IDs or 0)
    stale:           bool = False
    stale_reason:    str = ""
    source:          str = ""


@dataclass
class Matchup:
    enemy_en_id: str = ""
    win_rate:    float = 0.0   # 0–100
    sample_size: int = 0
    stale:       bool = False
    source:      str = ""


class StatsProvider(ABC):
    """抽象接口：给定英雄 + 位置，返回出装/符文/对位胜率。"""

    @abstractmethod
    def get_build(self, champ_en_id: str, role: str,
                  enemy_en_id: str = "") -> Optional[Build]:
        """role: top / jungle / middle / bottom / utility
        enemy_en_id 非空时返回针对该对位的出装（若支持）。"""

    @abstractmethod
    def get_runes(self, champ_en_id: str, role: str,
                  enemy_en_id: str = "") -> Optional[Runes]:
        """enemy_en_id 非空时返回针对该对位的符文（若支持）。"""

    def get_matchup(self, champ_en_id: str, role: str, enemy_en_id: str) -> Optional[Matchup]:
        """对位胜率；默认返回 None（子类按需实现）。"""
        return None

    def get_primary_role(self, champ_en_id: str) -> Optional[str]:
        """该英雄最常出现的位置；默认返回 None。"""
        return None
