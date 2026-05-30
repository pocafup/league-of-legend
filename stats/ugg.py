"""
U.GG stats provider — 当前不可用。

CDN stats2.u.gg 对所有路径返回 403；
GraphQL API 需要登录 token；
Build 数据在 PLUS 付费墙后。

保留此文件作为接口占位，将来如有可用端点再实现。
"""
from __future__ import annotations

from typing import Optional

from .provider import Build, Matchup, Runes, StatsProvider


class UggProvider(StatsProvider):
    def get_build(self, champ_en_id: str, role: str) -> Optional[Build]:
        print("  [U.GG] 暂不可用（需要付费订阅）")
        return None

    def get_runes(self, champ_en_id: str, role: str) -> Optional[Runes]:
        print("  [U.GG] 暂不可用（需要付费订阅）")
        return None
