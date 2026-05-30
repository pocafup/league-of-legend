"""
OP.GG stats provider — scrapes build / rune data from SSR HTML.
No authentication required; data is in the initial page HTML.

Cache key: cache/stats/opgg_{champ}_{role}_{tier}.json
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from .provider import Build, Matchup, Runes, StatsProvider

CACHE_DIR = Path("cache/stats")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://op.gg/",
}

# OP.GG uses different role slugs than LCU positions
ROLE_MAP = {
    "top":     "top",
    "jungle":  "jungle",
    "middle":  "mid",
    "bottom":  "bottom",
    "utility": "support",
}

# Cache TTL in seconds (4 hours)
CACHE_TTL = 4 * 3600

SOURCE = "OP.GG"


def _cache_path(champ_en_id: str, role: str, tier: str) -> Path:
    return CACHE_DIR / f"opgg_{champ_en_id.lower()}_{role}_{tier}.json"


def _load_cache(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - data.get("_ts", 0) < CACHE_TTL:
            return data
    except Exception:
        pass
    return None


def _save_cache(path: Path, data: dict) -> None:
    data["_ts"] = time.time()
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _extract_ids_from_imgs(tag, pattern: str) -> list[int]:
    """从 img[src] 里用正则提取数字 ID 列表。"""
    ids = []
    for img in tag.find_all("img"):
        src = img.get("src", "")
        m = re.search(pattern, src)
        if m:
            ids.append(int(m.group(1)))
    return ids


def _parse_items(soup: BeautifulSoup) -> dict:
    """
    返回 {starter, boots, core, fourth, fifth, sixth}，每项为 item ID 列表。
    OP.GG 把每个 section 放在独立的 <table> 里；
    第一个 <tr> 是 section 标题，第二个 <tr> 是最高胜率那行数据。
    """
    SECTION_LABELS = {
        "Starter Items": "starter",
        "Boots":         "boots",
        "Core Builds":   "core",
        "Fourth Item":   "fourth",
        "Fifth Item":    "fifth",
        "Sixth Item":    "sixth",
    }

    result: dict[str, list[int]] = {k: [] for k in SECTION_LABELS.values()}

    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header_text = rows[0].get_text(separator=" ", strip=True)
        key = None
        for label, field_name in SECTION_LABELS.items():
            if label.lower() in header_text.lower():
                key = field_name
                break
        if key is None:
            continue
        # 第二行是最高胜率数据行
        data_row = rows[1]
        ids = _extract_ids_from_imgs(data_row, r"/item/(\d+)\.png")
        if ids:
            result[key] = ids

    return result


def _parse_runes(soup: BeautifulSoup) -> dict:
    """
    返回 {primary_tree, secondary_tree, keystone, primary_perks, secondary_perks}。

    DOM 特征（已通过 HTML 分析确认）:
    - perkStyle IDs: 前两个 img[src*="/perkStyle/STYLEID.png"]
    - keystone: 含 basis-[120px] 祖先的第一个 perk img（opacity-100）
    - 已选符文（含主副系）：img[class*="opacity-100"][src*="/perk/"] 按 DOM 顺序排列。
      页面会把整套符文渲染两次（两个 section），故用 seen 集合去重。
      去重后顺序：keystone, 主系 row2/3/4 (3个), 副系 (2个) = 共 6 个。
    """
    result = {
        "primary_tree": 0,
        "secondary_tree": 0,
        "keystone": 0,
        "primary_perks": [],
        "secondary_perks": [],
    }

    # 主副系树 perkStyle — 取前两个不重复的 ID
    seen_styles: list[int] = []
    for img in soup.find_all("img", src=re.compile(r"/perkStyle/(\d+)\.png")):
        m = re.search(r"/perkStyle/(\d+)\.png", img["src"])
        if m:
            sid = int(m.group(1))
            if sid not in seen_styles:
                seen_styles.append(sid)
            if len(seen_styles) == 2:
                break
    if len(seen_styles) >= 1:
        result["primary_tree"] = seen_styles[0]
    if len(seen_styles) >= 2:
        result["secondary_tree"] = seen_styles[1]

    # 已选符文：image 的 class 含 "opacity-100" 且 src 含 "/perk/"
    # 页面渲染两份相同内容，用已见集合取第一次出现的顺序
    seen_perks: list[int] = []
    seen_ids: set[int] = set()
    for img in soup.find_all("img", src=re.compile(r"/perk/(\d+)\.png")):
        img_classes = " ".join(img.get("class", []))
        if "opacity-100" not in img_classes:
            continue
        m = re.search(r"/perk/(\d+)\.png", img["src"])
        if not m:
            continue
        pid = int(m.group(1))
        if pid not in seen_ids:
            seen_ids.add(pid)
            seen_perks.append(pid)

    # DOM 顺序：keystone 最先（在 basis-[120px] 容器里）→ 主系 3 个 → 副系 2 个
    # seen_perks 共 6 个（5 个也接受，表示其中一个解析失败）
    if seen_perks:
        result["keystone"] = seen_perks[0]
    if len(seen_perks) >= 4:
        result["primary_perks"]   = seen_perks[1:4]
        result["secondary_perks"] = seen_perks[4:6]
    elif len(seen_perks) >= 2:
        result["primary_perks"] = seen_perks[1:]

    return result


def _fetch_html(champ_en_id: str, role: str, tier: str) -> str:
    opgg_role = ROLE_MAP.get(role, role)
    url = (
        f"https://op.gg/lol/champions/{champ_en_id.lower()}/build"
        f"?region=global&tier={tier}&position={opgg_role}"
    )
    with httpx.Client(timeout=15, headers=HEADERS, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
    return resp.text


class OpggProvider(StatsProvider):
    def __init__(self, tier: str = "gold_plus", debug: bool = False):
        self.tier  = tier
        self.debug = debug

    def _get_data(self, champ_en_id: str, role: str) -> Optional[dict]:
        cache_path = _cache_path(champ_en_id, role, self.tier)
        cached = _load_cache(cache_path)
        if cached:
            return cached

        try:
            html = _fetch_html(champ_en_id, role, self.tier)
        except Exception as e:
            print(f"  [OP.GG] 抓取失败: {e}")
            # 尝试返回过期缓存
            if cache_path.exists():
                try:
                    data = json.loads(cache_path.read_text(encoding="utf-8"))
                    data["_stale"] = True
                    data["_stale_reason"] = f"网络错误: {e}"
                    return data
                except Exception:
                    pass
            return None

        if self.debug:
            debug_path = Path("cache") / f"debug_opgg_{champ_en_id.lower()}_{role}.html"
            debug_path.write_text(html, encoding="utf-8")
            print(f"  [OP.GG debug] HTML 已保存 → {debug_path}")

        soup = BeautifulSoup(html, "html.parser")

        items = _parse_items(soup)
        runes = _parse_runes(soup)

        data = {"items": items, "runes": runes, "_stale": False, "_stale_reason": ""}
        _save_cache(cache_path, data)
        return data

    def get_build(self, champ_en_id: str, role: str, enemy_en_id: str = "") -> Optional[Build]:
        data = self._get_data(champ_en_id, role)
        if not data:
            return None
        items = data.get("items", {})
        return Build(
            starter = items.get("starter", []),
            boots   = items.get("boots",   []),
            core    = items.get("core",    []),
            fourth  = items.get("fourth",  []),
            fifth   = items.get("fifth",   []),
            sixth   = items.get("sixth",   []),
            stale        = data.get("_stale", False),
            stale_reason = data.get("_stale_reason", ""),
            source       = SOURCE,
        )

    def get_runes(self, champ_en_id: str, role: str, enemy_en_id: str = "") -> Optional[Runes]:
        data = self._get_data(champ_en_id, role)
        if not data:
            return None
        runes = data.get("runes", {})
        return Runes(
            primary_tree    = runes.get("primary_tree",    0),
            secondary_tree  = runes.get("secondary_tree",  0),
            keystone        = runes.get("keystone",        0),
            primary_perks   = runes.get("primary_perks",   []),
            secondary_perks = runes.get("secondary_perks", []),
            stat_shards     = [],   # OP.GG stat shards 暂未解析
            stale        = data.get("_stale", False),
            stale_reason = data.get("_stale_reason", ""),
            source       = SOURCE,
        )

    def get_matchup(self, champ_en_id: str, role: str, enemy_en_id: str) -> Optional[Matchup]:
        # OP.GG matchup 数据通过 Server Action（动态 POST），hash 易变，暂不实现
        return None

    def get_primary_role(self, champ_en_id: str) -> Optional[str]:
        # 暂未实现；可通过分析各路胜率数据推断
        return None
