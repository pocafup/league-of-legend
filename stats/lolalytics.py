"""
Lolalytics stats provider.

数据来自 Lolalytics SSR 页面内嵌的 Qwik state（<script type="qwik/json">）。
对位专页 URL:
  https://lolalytics.com/lol/{champ}/vs/{enemy}/build/?lane={lane}&vslane={enemy_lane}&tier={tier}
通用出装 URL:
  https://lolalytics.com/lol/{champ}/build/?lane={lane}&tier={tier}

Qwik state 结构：{refs, ctx, objs, subs}
  objs 是序列化对象数组，字符串引用用 base-36 编码（int(ref, 36) = 下标）。
  主 build 对象特征字段：{boots, startItem, item1, header, n, summary}。

数据格式（已通过 explore_lolalytics*.py 验证）：
  startSet  → [["1055_2003", wr%, pick%, count], ...]
  boots     → [[item_id, wr%, pick%, count, timing], ...]  按 pick% 降序
  item1-5   → [[item_id, wr%, pick%, count, timing], ...]  按 pick% 降序
  header    → {wr: vs专属胜率, n: 样本量, patch, cid, vs, ...}
  summary.pick.runes.set → {pri: [keystone,p1,p2,p3], sec: [p1,p2], mod: [s1,s2,s3]}
  summary.pick.runes.page → {pri: 0-4 index, sec: 0-4 index}
    index→style: {0:8000, 1:8100, 2:8200, 3:8400, 4:8300}
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from .provider import Build, Matchup, Runes, StatsProvider

# ── 常量 ──────────────────────────────────────────────────────────────────────

CACHE_DIR = Path("cache/stats")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

RAW_DIR = Path("cache/lolalytics_raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://lolalytics.com/",
}

LANE_MAP = {
    "top":     "top",
    "jungle":  "jungle",
    "middle":  "middle",
    "bottom":  "bottom",
    "utility": "support",
}

# Lolalytics 0-4 索引 → DDragon perkStyle ID（已验证 0→8000 Precision, 3→8400 Resolve）
STYLE_IDX: dict[int, int] = {0: 8000, 1: 8100, 2: 8200, 3: 8400, 4: 8300}

CACHE_TTL = 4 * 3600      # 4 小时
MIN_SAMPLE = 200           # 样本量低于此值回退到通用出装
SOURCE = "Lolalytics"


# ── Qwik state 解码 ───────────────────────────────────────────────────────────

def _deref(ref, objs: list):
    """单步解引用：base-36 字符串 → objs[idx]。"""
    if isinstance(ref, str):
        try:
            idx = int(ref, 36)
            if 0 <= idx < len(objs):
                return objs[idx]
        except ValueError:
            pass
    return ref


def _dd(val, objs: list, depth: int = 0, max_d: int = 8):
    """递归解引用整个子树。"""
    if depth > max_d:
        return val
    if isinstance(val, str):
        v2 = _deref(val, objs)
        if v2 is not val:
            return _dd(v2, objs, depth + 1, max_d)
        return val
    if isinstance(val, dict):
        return {k: _dd(v, objs, depth + 1, max_d) for k, v in val.items()}
    if isinstance(val, list):
        return [_dd(v, objs, depth + 1, max_d) for v in val]
    return val


def _find_build_obj(objs: list) -> Optional[dict]:
    """找主 build 对象：含 boots/startItem/item1/header/n 的 dict。"""
    required = {"boots", "startItem", "item1", "header", "n"}
    for obj in objs:
        if isinstance(obj, dict) and required.issubset(obj.keys()):
            return obj
    return None


def _extract_qwik_state(html: str) -> Optional[list]:
    """从 HTML 中提取 Qwik SSR state 的 objs 数组。"""
    blocks = re.findall(
        r'<script[^>]*type=["\']qwik/json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    for blk in blocks:
        try:
            state = json.loads(blk)
            if "objs" in state:
                return state["objs"]
        except Exception:
            pass
    return None


# ── 数据提取 ──────────────────────────────────────────────────────────────────

def _first_item_id(data) -> Optional[int]:
    """从 [[item_id, wr, pick, count, ?timing], ...] 取最高 pick% 的 item_id。"""
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    if isinstance(first, list) and first:
        val = first[0]
        if isinstance(val, int) and val > 0:
            return val
    return None


def _norm_pct(v) -> float:
    """把 0-1 或 0-100 的百分比统一到 0-100 范围。"""
    if not isinstance(v, (int, float)):
        return 0.0
    f = float(v)
    return f * 100 if 0 < f <= 1.0 else f


def _parse_item_tables(objs: list, build_obj: dict) -> dict:
    """
    提取每个装备槽的完整排名列表，用于多对位混合打分。
    返回 {"item1": [{"id":..,"wr":..,"pick":..,"count":..}, ...], ...}
    """
    result: dict[str, list[dict]] = {}

    for slot in ("item1", "item2", "item3", "item4", "item5", "boots"):
        raw = _dd(build_obj.get(slot, []), objs)
        entries: list[dict] = []
        if isinstance(raw, list):
            for row in raw:
                if not isinstance(row, list) or len(row) < 4:
                    continue
                item_id, wr_v, pk_v, cnt_v = row[0], row[1], row[2], row[3]
                if not isinstance(item_id, int) or item_id <= 0:
                    continue
                entries.append({
                    "id":    item_id,
                    "wr":    _norm_pct(wr_v),
                    "pick":  _norm_pct(pk_v),
                    "count": int(cnt_v) if isinstance(cnt_v, (int, float)) else 0,
                })
        result[slot] = entries

    # 起手套装
    startset = _dd(build_obj.get("startSet", []), objs)
    start_entries: list[dict] = []
    if isinstance(startset, list):
        for row in startset:
            if not isinstance(row, list) or not row:
                continue
            raw_str = row[0]
            ids = [int(x) for x in str(raw_str).split("_") if isinstance(x, str) and x.isdigit()
                   or isinstance(x, int) and x > 0]
            if not ids:
                continue
            wr_v  = row[1] if len(row) > 1 else 0
            pk_v  = row[2] if len(row) > 2 else 0
            cnt_v = row[3] if len(row) > 3 else 0
            start_entries.append({
                "ids":   ids,
                "wr":    _norm_pct(wr_v),
                "pick":  _norm_pct(pk_v),
                "count": int(cnt_v) if isinstance(cnt_v, (int, float)) else 0,
            })
    result["startSet"] = start_entries

    return result


def _parse_build(objs: list, build_obj: dict) -> dict:
    """从主 build 对象提取出装数据。"""
    # 起手套装
    startset = _dd(build_obj.get("startSet", []), objs)
    starter: list[int] = []
    if isinstance(startset, list) and startset and isinstance(startset[0], list):
        raw_str = startset[0][0]
        if isinstance(raw_str, (str, int)):
            starter = [int(x) for x in str(raw_str).split("_") if x.isdigit()]

    # 鞋子
    boots_data = _dd(build_obj.get("boots", []), objs)
    boots: list[int] = []
    bid = _first_item_id(boots_data)
    if bid:
        boots = [bid]

    # 核心三件（item1 / item2 / item3，按 pick% 最高选）
    core: list[int] = []
    seen: set[int] = set(starter + boots)
    for key in ("item1", "item2", "item3"):
        data = _dd(build_obj.get(key, []), objs)
        iid = _first_item_id(data)
        if iid and iid not in seen:
            core.append(iid)
            seen.add(iid)

    # 按需（item4 / item5）
    fourth: list[int] = []
    fifth: list[int] = []
    for key, bucket in (("item4", fourth), ("item5", fifth)):
        data = _dd(build_obj.get(key, []), objs)
        iid = _first_item_id(data)
        if iid and iid not in seen:
            bucket.append(iid)
            seen.add(iid)

    return {
        "starter": starter, "boots": boots, "core": core,
        "fourth": fourth, "fifth": fifth, "sixth": [],
    }


def _parse_runes(objs: list, build_obj: dict) -> dict:
    """
    从 summary.pick.runes 提取符文数据。
    用显式逐步解引用，避免递归深度不足。
    链：summary→dict→pick→dict→runes→dict→page/set→dict→各字段
    """
    def d(ref):
        return _deref(ref, objs)

    def resolve_int_list(ref) -> list[int]:
        lst = d(ref)
        if not isinstance(lst, list):
            return []
        return [x for x in (d(v) for v in lst) if isinstance(x, int)]

    summary_obj = d(build_obj.get("summary"))
    if not isinstance(summary_obj, dict):
        return {}

    pick_obj = d(summary_obj.get("pick"))
    if not isinstance(pick_obj, dict):
        return {}

    runes_obj = d(pick_obj.get("runes"))
    if not isinstance(runes_obj, dict):
        return {}

    page_obj = d(runes_obj.get("page"))
    set_obj  = d(runes_obj.get("set"))

    if not isinstance(page_obj, dict) or not isinstance(set_obj, dict):
        return {}

    pri_style = STYLE_IDX.get(d(page_obj.get("pri")), 0)
    sec_style = STYLE_IDX.get(d(page_obj.get("sec")), 0)

    pri_perks  = resolve_int_list(set_obj.get("pri"))  # [keystone, p1, p2, p3]
    sec_perks  = resolve_int_list(set_obj.get("sec"))  # [p1, p2]
    stat_mods  = resolve_int_list(set_obj.get("mod"))  # [s1, s2, s3]

    keystone      = pri_perks[0] if pri_perks else 0
    primary_perks = pri_perks[1:4]

    return {
        "primary_tree":    pri_style,
        "secondary_tree":  sec_style,
        "keystone":        keystone,
        "primary_perks":   primary_perks,
        "secondary_perks": sec_perks[:2],
        "stat_shards":     stat_mods[:3],
    }


def _parse_header(objs: list, build_obj: dict) -> dict:
    """提取 header 字段（胜率/样本量）。"""
    return _dd(build_obj.get("header", {}), objs, max_d=3)


def _parse_sample(objs: list, build_obj: dict) -> int:
    """提取 n（样本量）。"""
    n = _dd(build_obj.get("n", 0), objs, max_d=2)
    return int(n) if isinstance(n, (int, float)) else 0


# ── 缓存 ──────────────────────────────────────────────────────────────────────

def _cache_path(champ: str, role: str, tier: str, enemy: str = "") -> Path:
    suffix = f"_{enemy.lower()}" if enemy else ""
    return CACHE_DIR / f"loly_{champ.lower()}_{role}_{tier}{suffix}.json"


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


# ── HTTP 抓取 + 解析 ──────────────────────────────────────────────────────────

def _fetch_page(champ: str, role: str, tier: str, enemy: str = "") -> Optional[dict]:
    """抓取 HTML，提取并解析 build/runes/header 数据。"""
    lane = LANE_MAP.get(role, role)
    if enemy:
        url = (
            f"https://lolalytics.com/lol/{champ.lower()}/vs/{enemy.lower()}/build/"
            f"?lane={lane}&vslane={lane}&tier={tier}"
        )
    else:
        url = (
            f"https://lolalytics.com/lol/{champ.lower()}/build/"
            f"?lane={lane}&tier={tier}"
        )

    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=20) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        print(f"  [Lolalytics] 抓取失败 ({champ}{' vs '+enemy if enemy else ''}): {e}")
        return None

    objs = _extract_qwik_state(html)
    if not objs:
        print(f"  [Lolalytics] 未找到 qwik/json state ({champ})")
        return None

    build_obj = _find_build_obj(objs)
    if not build_obj:
        print(f"  [Lolalytics] 未找到 build 对象 ({champ})")
        return None

    header = _parse_header(objs, build_obj)
    n = int(header.get("n", 0)) if isinstance(header, dict) else _parse_sample(objs, build_obj)
    wr = header.get("wr") if isinstance(header, dict) else None
    patch = header.get("patch", "") if isinstance(header, dict) else ""

    build_data  = _parse_build(objs, build_obj)
    rune_data   = _parse_runes(objs, build_obj)
    item_tables = _parse_item_tables(objs, build_obj)

    return {
        "build": build_data,
        "runes": rune_data,
        "item_tables": item_tables,
        "n": n,
        "wr": wr,
        "patch": patch,
        "_stale": False,
        "_stale_reason": "",
    }


# ── Provider 实现 ─────────────────────────────────────────────────────────────

class LolalyticsProvider(StatsProvider):
    """
    首选 stats provider。
    若对位样本不足（< MIN_SAMPLE），自动回退到通用出装，
    并在 Build.source / Runes.source 标注来源信息。
    """

    def __init__(self, tier: str = "emerald_plus", debug: bool = False):
        self.tier  = tier
        self.debug = debug

    # ── 内部：获取数据（含缓存 + 回退） ──────────────────────────────────────

    def _get_data(
        self, champ: str, role: str, enemy: str = ""
    ) -> tuple[Optional[dict], bool, str]:
        """
        返回 (data, used_general_fallback, source_label)。
        先尝试对位专页，样本不足时回退通用。
        """
        # 1. 对位专页（若给出 enemy）
        if enemy:
            cp = _cache_path(champ, role, self.tier, enemy)
            cached = _load_cache(cp)
            if cached:
                n = cached.get("n", 0)
                if n >= MIN_SAMPLE:
                    return cached, False, self._src_label(champ, role, enemy, cached)
                # 缓存样本不足 → 继续走通用（不重新抓取）
            else:
                data = _fetch_page(champ, role, self.tier, enemy)
                if data:
                    _save_cache(cp, data)
                    if self.debug:
                        (RAW_DIR / f"vs_{champ.lower()}_{enemy.lower()}_{role}.json").write_text(
                            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                    if data["n"] >= MIN_SAMPLE:
                        return data, False, self._src_label(champ, role, enemy, data)
                    # 样本不足 → 继续走通用
                    print(f"  [Lolalytics] {champ} vs {enemy} 样本 {data['n']} < {MIN_SAMPLE}，回退通用配置")

        # 2. 通用页
        gp = _cache_path(champ, role, self.tier)
        cached_g = _load_cache(gp)
        if cached_g:
            return cached_g, True, self._src_label(champ, role, "", cached_g)

        data_g = _fetch_page(champ, role, self.tier)
        if data_g:
            _save_cache(gp, data_g)
            return data_g, True, self._src_label(champ, role, "", data_g)

        # 3. 尝试用 stale cache
        for path, is_gen in [(gp, True), (_cache_path(champ, role, self.tier, enemy) if enemy else None, False)]:
            if path and path.exists():
                try:
                    d = json.loads(path.read_text(encoding="utf-8"))
                    d["_stale"] = True
                    d["_stale_reason"] = "网络错误，使用过期缓存"
                    return d, is_gen, self._src_label(champ, role, "" if is_gen else enemy, d) + " ⚠"
                except Exception:
                    pass

        return None, True, ""

    def _src_label(self, champ: str, role: str, enemy: str, data: dict) -> str:
        n = data.get("n", 0)
        patch = data.get("patch", "")
        tier_disp = self.tier.replace("_", " ").title().replace("Plus", "+")
        if enemy:
            return f"{SOURCE} · {tier_disp} · vs {enemy} · {n:,} 局 · patch {patch}"
        return f"{SOURCE} · {tier_disp} · 通用 · {n:,} 局 · patch {patch}"

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def get_build(self, champ_en_id: str, role: str,
                  enemy_en_id: str = "") -> Optional[Build]:
        data, fallback, label = self._get_data(champ_en_id, role, enemy_en_id)
        if not data:
            return None
        b = data.get("build", {})
        src = label
        if fallback and enemy_en_id:
            src += " （对位样本不足，显示通用配置）"
        return Build(
            starter = b.get("starter", []),
            boots   = b.get("boots",   []),
            core    = b.get("core",    []),
            fourth  = b.get("fourth",  []),
            fifth   = b.get("fifth",   []),
            sixth   = b.get("sixth",   []),
            stale        = data.get("_stale", False),
            stale_reason = data.get("_stale_reason", ""),
            source       = src,
        )

    def get_runes(self, champ_en_id: str, role: str,
                  enemy_en_id: str = "") -> Optional[Runes]:
        data, fallback, label = self._get_data(champ_en_id, role, enemy_en_id)
        if not data:
            return None
        r = data.get("runes", {})
        if not r:
            return None
        src = label
        if fallback and enemy_en_id:
            src += " （对位样本不足，显示通用配置）"
        return Runes(
            primary_tree    = r.get("primary_tree",    0),
            secondary_tree  = r.get("secondary_tree",  0),
            keystone        = r.get("keystone",        0),
            primary_perks   = r.get("primary_perks",   []),
            secondary_perks = r.get("secondary_perks", []),
            stat_shards     = r.get("stat_shards",     []),
            stale        = data.get("_stale", False),
            stale_reason = data.get("_stale_reason", ""),
            source       = src,
        )

    def get_matchup(self, champ_en_id: str, role: str,
                    enemy_en_id: str) -> Optional[Matchup]:
        data, fallback, _ = self._get_data(champ_en_id, role, enemy_en_id)
        if not data or fallback:
            return None
        wr = data.get("wr")
        n  = data.get("n", 0)
        if wr is None:
            return None
        return Matchup(
            enemy_en_id = enemy_en_id,
            win_rate    = float(wr),
            sample_size = int(n),
            stale       = data.get("_stale", False),
            source      = SOURCE,
        )

    def get_primary_role(self, champ_en_id: str) -> Optional[str]:
        return None

    def get_item_table(
        self, champ_en_id: str, role: str, enemy_en_id: str, min_n: int = 200
    ) -> Optional[dict]:
        """
        返回 my vs enemy 的完整装备排名表（item1-5 + boots + startSet）。
        样本 < min_n 返回 None（不回退通用，保持混合数据纯净）。
        已有缓存但缺少 item_tables 字段时自动重新抓取。
        """
        if not enemy_en_id:
            return None
        cp = _cache_path(champ_en_id, role, self.tier, enemy_en_id)
        cached = _load_cache(cp)

        # 旧缓存没有 item_tables 字段，需要重新抓取
        if cached and "item_tables" not in cached:
            cached = None

        if cached is None:
            data = _fetch_page(champ_en_id, role, self.tier, enemy_en_id)
            if data:
                _save_cache(cp, data)
                cached = data

        if cached is None:
            return None

        n = cached.get("n", 0)
        if n < min_n:
            print(f"  [Lolalytics] {champ_en_id} vs {enemy_en_id} 样本 {n} < {min_n}，跳过混合")
            return None

        return {**cached.get("item_tables", {}), "n": n, "patch": cached.get("patch", "")}
