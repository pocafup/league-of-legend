"""
Data Dragon 客户端：拉取/缓存 champion.json / item.json / runesReforged.json，
提供 championId → 英雄信息、itemId → 装备名、runeId/styleId → 符文名映射。

已确认字段：
  champion.json  entry["key"] = 数字型 championId 字符串；entry["id"] = 英文标识；entry["name"] = 中文名
  item.json      data[str(id)]["name"] = 中文名
  runesReforged  列表；每项 {id, name(中文), slots:[{runes:[{id, name(中文)}]}]}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from difflib import get_close_matches

import httpx

DDRAGON_BASE = "https://ddragon.leagueoflegends.com"

# 统计碎片 ID → 中文名（Lolalytics 使用 5000-5013，不在 runesReforged 内）
_STAT_SHARD_NAMES: dict[int, str] = {
    5001: "生命值",
    5002: "护甲",
    5003: "魔法抗性",
    5005: "攻击速度",
    5007: "技能急速",
    5008: "自适应强度",
    5010: "法术穿透",
    5011: "生命值(进阶)",
    5013: "韧性+减速抗性",
}

# 常用英雄昵称 → DDragon 英文 ID（昵称与 DDragon 中文名不一致时使用）
_NICKNAME_TO_EN_ID: dict[str, str] = {
    "船长": "Gangplank",
    "诺手": "Darius",
    "妖姬": "Ahri",
    "劫":   "Zed",
    "刀妹": "Fiora",
    "石头人": "Malphite",
    "狗头": "Nasus",
    "蛮王": "Tryndamere",
    "螃蟹": "Urgot",
    "女枪": "MissFortune",
    "男枪": "Graves",
    "锤石": "Thresh",
    "铁男": "Mordekaiser",
    "猫咪": "Yuumi",
    "酒桶": "Gragas",
    "蜘蛛": "Elise",
    "煤球": "Nocturne",
    "猴子": "MonkeyKing",
    "悟空": "MonkeyKing",
    "大树": "Maokai",
    "老鼠": "Twitch",
    "小炮": "Corki",
    "风女": "Janna",
    "牛头": "Alistar",
    "小鱼人": "Fizz",
    "寡妇": "Zyra",
    "斧头": "Darius",
    "ez":  "Ezreal",
    "EZ":  "Ezreal",
}


@dataclass(frozen=True)
class ChampionInfo:
    numeric_id: int  # LCU 里的 championId（整数）
    en_id: str       # DDragon 英文标识，如 "Gangplank"（统计站查询用）
    zh_name: str     # 中文名，如 "船长"
    tags: tuple[str, ...] = ()  # DDragon tags, e.g. ("Fighter", "Tank")


class DDragonClient:
    def __init__(self, cache_dir: Path, lang: str = "zh_CN"):
        self._cache_dir = cache_dir
        self._lang = lang
        self._version: str = ""
        # champion
        self._by_id: dict[int, ChampionInfo] = {}
        # item
        self._item_names: dict[int, str] = {}
        # runes
        self._perk_names:  dict[int, str] = {}   # perk id → 中文名
        self._style_names: dict[int, str] = {}   # perkStyle id → 中文名
        self._perk_icons:  dict[int, str] = {}   # perk id → icon 相对路径
        self._style_icons: dict[int, str] = {}   # perkStyle id → icon 相对路径

    # ── 公开接口 ─────────────────────────────────────────────────────────────

    def ensure_loaded(self) -> str:
        """确保全部数据已加载并返回当前版本号；已加载则直接返回。"""
        if self._by_id:
            return self._version

        with httpx.Client(timeout=15) as client:
            self._version = self._fetch_latest_version(client)
            champ_raw = self._load_file(client, "champion.json")
            item_raw  = self._load_file(client, "item.json")
            runes_raw = self._load_file(client, "runesReforged.json", is_list=True)

        self._build_champ_maps(champ_raw)
        self._build_item_maps(item_raw)
        self._build_rune_maps(runes_raw)
        return self._version

    # champion

    def get(self, numeric_id: int) -> ChampionInfo | None:
        return self._by_id.get(numeric_id)

    def get_or_unknown(self, numeric_id: int) -> ChampionInfo:
        return self._by_id.get(numeric_id) or ChampionInfo(
            numeric_id=numeric_id,
            en_id="Unknown",
            zh_name=f"未知英雄({numeric_id})",
            tags=(),
        )

    def get_by_en_id(self, en_id: str) -> ChampionInfo | None:
        for info in self._by_id.values():
            if info.en_id == en_id:
                return info
        return None

    def rune_icon_path(self, perk_id: int) -> str:
        return self._perk_icons.get(perk_id, "")

    def style_icon_path(self, style_id: int) -> str:
        return self._style_icons.get(style_id, "")

    def champ_tags(self, en_id: str) -> list[str]:
        """返回英雄的 DDragon tags（如 ['Fighter', 'Tank']），找不到返回空列表。"""
        for info in self._by_id.values():
            if info.en_id == en_id:
                return list(info.tags)
        return []

    def find_champ(self, query: str) -> list[ChampionInfo]:
        """
        模糊搜索英雄，返回匹配列表。
        空列表 = 找不到；1 个 = 精确；多个 = 候选（调用方提示用户）。
        匹配顺序：昵称表 → 精确中文名 → 精确英文ID → 中文子串 → 英文子串 → difflib。
        """
        if not self._by_id:
            return []
        q = query.strip()
        if not q:
            return []

        # 1. 昵称表
        if q in _NICKNAME_TO_EN_ID:
            en_id = _NICKNAME_TO_EN_ID[q]
            for info in self._by_id.values():
                if info.en_id == en_id:
                    return [info]

        # 2. 精确中文名
        for info in self._by_id.values():
            if info.zh_name == q:
                return [info]

        # 3. 精确英文 ID（大小写不敏感）
        q_lower = q.lower()
        for info in self._by_id.values():
            if info.en_id.lower() == q_lower:
                return [info]

        # 4. 中文名包含 query（至少 2 字）
        if len(q) >= 2:
            zh_sub = [info for info in self._by_id.values() if q in info.zh_name]
            if zh_sub:
                return zh_sub

        # 5. 英文 ID 包含 query（大小写不敏感）
        en_sub = [info for info in self._by_id.values() if q_lower in info.en_id.lower()]
        if en_sub:
            return sorted(en_sub, key=lambda x: len(x.en_id))

        # 6. difflib 模糊（≥2 字符）
        if len(q) >= 2:
            all_zh = {info.zh_name: info for info in self._by_id.values()}
            close = get_close_matches(q, all_zh.keys(), n=5, cutoff=0.5)
            if close:
                return [all_zh[n] for n in close]
            all_en = {info.en_id.lower(): info for info in self._by_id.values()}
            close_en = get_close_matches(q_lower, all_en.keys(), n=5, cutoff=0.5)
            if close_en:
                return [all_en[n] for n in close_en]

        return []

    # item / rune 翻译（查不到时降级显示，不崩溃）

    def item_name(self, item_id: int) -> str:
        name = self._item_names.get(item_id)
        if name is None:
            print(f"  [ddragon] 警告：未知装备 ID={item_id}")
            return f"未知装备({item_id})"
        return name

    def rune_name(self, perk_id: int) -> str:
        name = self._perk_names.get(perk_id)
        if name is None:
            # 尝试统计碎片
            shard = _STAT_SHARD_NAMES.get(perk_id)
            if shard:
                return shard
            print(f"  [ddragon] 警告：未知符文 ID={perk_id}")
            return f"未知符文({perk_id})"
        return name

    def rune_style_name(self, style_id: int) -> str:
        name = self._style_names.get(style_id)
        if name is None:
            print(f"  [ddragon] 警告：未知符文系 ID={style_id}")
            return f"未知系({style_id})"
        return name

    # ── 内部实现 ──────────────────────────────────────────────────────────────

    def _fetch_latest_version(self, client: httpx.Client) -> str:
        resp = client.get(f"{DDRAGON_BASE}/api/versions.json")
        resp.raise_for_status()
        return resp.json()[0]

    def _cache_path(self, filename: str) -> Path:
        stem = filename.replace(".json", "")
        return self._cache_dir / f"ddragon_{stem}_{self._version}_{self._lang}.json"

    def _load_file(self, client: httpx.Client, filename: str, is_list: bool = False):
        """从缓存或网络加载 DDragon 数据文件，返回 dict（或 list）。"""
        cache_file = self._cache_path(filename)
        if cache_file.exists():
            print(f"[ddragon] 从缓存读取  {filename}  文件={cache_file.name}")
            return json.loads(cache_file.read_text(encoding="utf-8"))

        url = f"{DDRAGON_BASE}/cdn/{self._version}/data/{self._lang}/{filename}"
        print(f"[ddragon] 下载 {filename}  版本={self._version}")
        resp = client.get(url)
        resp.raise_for_status()
        raw = resp.json()
        cache_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        print(f"[ddragon] 已缓存 → {cache_file.name}")
        return raw

    def _build_champ_maps(self, raw: dict) -> None:
        for entry in raw["data"].values():
            numeric_id = int(entry["key"])
            self._by_id[numeric_id] = ChampionInfo(
                numeric_id=numeric_id,
                en_id=entry["id"],
                zh_name=entry["name"],
                tags=tuple(entry.get("tags", [])),
            )
        print(f"[ddragon] 已建立 {len(self._by_id)} 个英雄映射")

    def _build_item_maps(self, raw: dict) -> None:
        for id_str, entry in raw.get("data", {}).items():
            self._item_names[int(id_str)] = entry["name"]
        print(f"[ddragon] 已建立 {len(self._item_names)} 个装备映射")

    def _build_rune_maps(self, raw) -> None:
        # raw 是列表：[{id, name, icon, slots:[{runes:[{id, name, icon}]}]}, ...]
        for style in raw:
            self._style_names[style["id"]] = style["name"]
            self._style_icons[style["id"]] = style.get("icon", "")
            for slot in style.get("slots", []):
                for perk in slot.get("runes", []):
                    self._perk_names[perk["id"]] = perk["name"]
                    self._perk_icons[perk["id"]] = perk.get("icon", "")
        print(f"[ddragon] 已建立 {len(self._style_names)} 个符文系 + {len(self._perk_names)} 个符文映射")
