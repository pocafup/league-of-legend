"""
LOL 选人助手 — FastAPI Web 后端（服务端，不直接读 LCU）
启动: uv run python server.py
      或: uv run uvicorn server:app --reload --port 8765

阵容由本机 agent.py 通过 POST /api/session 推送过来。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Query, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv()

from data.ddragon import DDragonClient
from stats.lolalytics import LolalyticsProvider
from advisor.session import POSITION_CN, TeamMember, parse_lcu_session, enrich_session
from advisor.comp_adjust import analyze_enemy_comp, comp_summary_for_claude
from advisor.tips import generate_tips

CACHE_DIR = Path(__file__).parent / "cache"
WEB_DIR   = Path(__file__).parent / "web"
WEB_DIR.mkdir(exist_ok=True)

# ── 鉴权 ──────────────────────────────────────────────────────────────────────

PUSH_TOKEN = os.environ.get("LEAGUE_PUSH_TOKEN", "")

def _check_push_token(request: Request) -> None:
    """验证 agent 推送请求。未配置 token 时跳过（本地开发）。"""
    if not PUSH_TOKEN:
        return
    auth = request.headers.get("X-Push-Token", "") or request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        auth = auth[7:]
    if auth != PUSH_TOKEN:
        raise HTTPException(status_code=401, detail="invalid push token")

def _check_page_token(token: Optional[str] = Query(None)) -> None:
    """验证页面/API 访问 token（URL ?token=...）。未配置 token 时跳过。"""
    if not PUSH_TOKEN:
        return
    if token != PUSH_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")


# ── 全局单例（启动时初始化一次）──────────────────────────────────────────────
print("[*] 加载 Data Dragon…")
dd = DDragonClient(cache_dir=CACHE_DIR)
dd.ensure_loaded()
print(f"[*] DDragon {dd._version} 就绪\n")

DDRAGON_CDN = f"https://ddragon.leagueoflegends.com/cdn/{dd._version}"

# 最近一次 agent 推送的原始 session（内存存储）
_current_session: Optional[dict] = None

# ── 内置演示会话（荒漠屠夫上单 vs 诺手，排位赛）────────────────────────────────
# 永久可用，不依赖磁盘文件，?demo=1 和 replay 文件缺失时均回退到此处。
_DEMO_SESSION: dict = {
    "actions": [[
        {"actorCellId": 0, "championId": 58,  "completed": True, "isAllyAction": True,  "type": "pick"},
        {"actorCellId": 1, "championId": 120, "completed": True, "isAllyAction": True,  "type": "pick"},
        {"actorCellId": 2, "championId": 103, "completed": True, "isAllyAction": True,  "type": "pick"},
        {"actorCellId": 3, "championId": 222, "completed": True, "isAllyAction": True,  "type": "pick"},
        {"actorCellId": 4, "championId": 412, "completed": True, "isAllyAction": True,  "type": "pick"},
        {"actorCellId": 5, "championId": 122, "completed": True, "isAllyAction": False, "type": "pick"},
        {"actorCellId": 6, "championId": 64,  "completed": True, "isAllyAction": False, "type": "pick"},
        {"actorCellId": 7, "championId": 238, "completed": True, "isAllyAction": False, "type": "pick"},
        {"actorCellId": 8, "championId": 51,  "completed": True, "isAllyAction": False, "type": "pick"},
        {"actorCellId": 9, "championId": 117, "completed": True, "isAllyAction": False, "type": "pick"},
    ]],
    "isCustomGame": False,
    "localPlayerCellId": 0,
    "queueId": 420,
    "timer": {"phase": "FINALIZATION"},
    "myTeam": [
        {"cellId": 0, "championId": 58,  "assignedPosition": "top",     "gameName": "pocafup", "team": 1},
        {"cellId": 1, "championId": 120, "assignedPosition": "jungle",  "gameName": "Ally1",   "team": 1},
        {"cellId": 2, "championId": 103, "assignedPosition": "middle",  "gameName": "Ally2",   "team": 1},
        {"cellId": 3, "championId": 222, "assignedPosition": "bottom",  "gameName": "Ally3",   "team": 1},
        {"cellId": 4, "championId": 412, "assignedPosition": "utility", "gameName": "Ally4",   "team": 1},
    ],
    "theirTeam": [
        {"cellId": 5, "championId": 122, "assignedPosition": "top",     "gameName": "Enemy1", "team": 2},
        {"cellId": 6, "championId": 64,  "assignedPosition": "jungle",  "gameName": "Enemy2", "team": 2},
        {"cellId": 7, "championId": 238, "assignedPosition": "middle",  "gameName": "Enemy3", "team": 2},
        {"cellId": 8, "championId": 51,  "assignedPosition": "bottom",  "gameName": "Enemy4", "team": 2},
        {"cellId": 9, "championId": 117, "assignedPosition": "utility", "gameName": "Enemy5", "team": 2},
    ],
}

app = FastAPI(title="LOL 选人助手")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/web", StaticFiles(directory=str(WEB_DIR)), name="web")


# ── 图标 URL 辅助 ─────────────────────────────────────────────────────────────

def _champ_icon(en_id: str) -> str:
    return f"{DDRAGON_CDN}/img/champion/{en_id}.png" if en_id else ""

def _item_icon(item_id: int) -> str:
    return f"{DDRAGON_CDN}/img/item/{item_id}.png"

def _rune_icon(perk_id: int) -> str:
    p = dd.rune_icon_path(perk_id)
    return f"https://ddragon.leagueoflegends.com/cdn/img/{p}" if p else ""

def _style_icon(style_id: int) -> str:
    p = dd.style_icon_path(style_id)
    return f"https://ddragon.leagueoflegends.com/cdn/img/{p}" if p else ""

def _item_d(iid: int) -> dict:
    return {"id": iid, "name": dd.item_name(iid), "icon_url": _item_icon(iid)}

def _perk_d(pid: int) -> dict:
    return {"id": pid, "name": dd.rune_name(pid), "icon_url": _rune_icon(pid)}

def _style_d(sid: int) -> dict:
    return {"id": sid, "name": dd.rune_style_name(sid), "icon_url": _style_icon(sid)}


def _member_dict(m: TeamMember) -> dict:
    return {
        "cell_id":       m.cell_id,
        "numeric_id":    m.champ.numeric_id,
        "en_id":         m.champ.en_id,
        "zh_name":       m.champ.zh_name,
        "avatar_url":    _champ_icon(m.champ.en_id) if m.champ.numeric_id else "",
        "position":      m.position,
        "inferred_pos":  m.inferred_pos,
        "effective_pos": m.effective_position,
        "position_cn":   m.position_cn,
        "pos_confidence": m.pos_confidence,
        "is_local":      m.is_local,
    }


# ── Pydantic 请求体 ───────────────────────────────────────────────────────────

class ChampRef(BaseModel):
    en_id: str
    zh_name: str = ""
    lane: str  # top / jungle / middle / bottom / utility

class AdviseReq(BaseModel):
    my_en_id:   str
    my_lane:    str
    my_team:    list[ChampRef]
    enemy_team: list[ChampRef]
    tier:       str = "emerald_plus"

class SessionPush(BaseModel):
    raw: dict   # 原始 LCU /lol-champ-select/v1/session JSON


# ── 路由 ──────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return FileResponse(WEB_DIR / "index.html")


@app.post("/api/session")
def push_session(body: SessionPush, _: None = Depends(_check_push_token)):
    """agent 推送当前选人会话。鉴权：X-Push-Token 请求头。"""
    global _current_session
    _current_session = body.raw
    return {"ok": True}


def _parse_raw(raw: dict) -> dict:
    """公用：解析原始 LCU dict → 前端所需 JSON。"""
    ds = parse_lcu_session(raw, dd)
    enrich_session(ds)
    return {
        "in_champ_select": True,
        "queue_id":  ds.queue_id,
        "is_custom": ds.is_custom,
        "my_team":    [_member_dict(m) for m in ds.my_team],
        "enemy_team": [_member_dict(m) for m in ds.enemy_team],
        "version":   dd._version,
    }


@app.get("/api/demo")
def get_demo(_: None = Depends(_check_page_token)):
    """返回内置演示会话（荒漠屠夫上单 vs 诺手）。永久可用，不依赖 agent 或磁盘文件。"""
    return _parse_raw(_DEMO_SESSION)


@app.get("/api/draft")
def get_draft(
    replay: Optional[str] = Query(None),
    _: None = Depends(_check_page_token),
):
    """
    返回当前阵容。
    - ?replay=<path>: 从本地文件读；文件不存在时自动回退内置演示数据
    - 否则返回 agent 最近推上来的会话；没有时返回等待状态
    """
    if replay:
        try:
            raw = json.loads(Path(replay).read_text(encoding="utf-8"))
            return _parse_raw(raw)
        except FileNotFoundError:
            return _parse_raw(_DEMO_SESSION)   # 文件缺失→回退演示数据，不报错
        except Exception as e:
            return {"in_champ_select": False, "reason": f"replay 读取失败: {e}"}

    if _current_session is None:
        return {"in_champ_select": False, "reason": "等待客户端连接…"}
    return _parse_raw(_current_session)


# ── 内部辅助 ──────────────────────────────────────────────────────────────────

def _opponent(req: AdviseReq) -> tuple[str, str]:
    """从请求里找对线对手 (en_id, zh_name)。"""
    for e in req.enemy_team:
        if e.lane == req.my_lane:
            info = dd.get_by_en_id(e.en_id)
            return e.en_id, (info.zh_name if info else e.zh_name)
    return "", ""


def _provider(tier: str) -> LolalyticsProvider:
    return LolalyticsProvider(tier=tier)


def _text_descs(req: AdviseReq, opp_en: str, prov: LolalyticsProvider) -> tuple[str, str]:
    build_desc = "出装：暂无数据"
    runes_desc = "符文：暂无数据"
    try:
        b = prov.get_build(req.my_en_id, req.my_lane, opp_en)
        if b:
            parts = []
            if b.starter: parts.append("起手：" + " → ".join(dd.item_name(i) for i in b.starter))
            if b.boots:   parts.append("鞋子：" + " → ".join(dd.item_name(i) for i in b.boots))
            if b.core:    parts.append("核心：" + " → ".join(dd.item_name(i) for i in b.core))
            sit = b.fourth + b.fifth + b.sixth
            if sit:       parts.append("按需：" + " / ".join(dd.item_name(i) for i in sit))
            if parts:     build_desc = "出装：" + "；".join(parts)
    except Exception:
        pass
    try:
        r = prov.get_runes(req.my_en_id, req.my_lane, opp_en)
        if r:
            pri = dd.rune_style_name(r.primary_tree)
            ks  = dd.rune_name(r.keystone) if r.keystone else "无"
            pp  = " / ".join(dd.rune_name(i) for i in r.primary_perks) if r.primary_perks else "无"
            sec = dd.rune_style_name(r.secondary_tree)
            sp  = " / ".join(dd.rune_name(i) for i in r.secondary_perks) if r.secondary_perks else "无"
            runes_desc = f"符文：主系{pri}（{ks}），{pp}；副系{sec}，{sp}"
    except Exception:
        pass
    return build_desc, runes_desc


# ── 接口：出装 / 符文 / 胜率（快，无 Claude）─────────────────────────────────

@app.post("/api/advise/build")
def advise_build(req: AdviseReq, _: None = Depends(_check_page_token)):
    prov = _provider(req.tier)
    opp_en, opp_zh = _opponent(req)

    build_d = runes_d = matchup_d = None

    try:
        b = prov.get_build(req.my_en_id, req.my_lane, opp_en)
        if b:
            sit: list[int] = []
            seen: set[int] = set()
            for iid in b.fourth + b.fifth + b.sixth:
                if iid not in seen:
                    sit.append(iid); seen.add(iid)
            build_d = {
                "source": b.source, "stale": b.stale, "stale_reason": b.stale_reason,
                "starter":     [_item_d(i) for i in b.starter],
                "boots":       [_item_d(i) for i in b.boots],
                "core":        [_item_d(i) for i in b.core],
                "situational": [_item_d(i) for i in sit],
            }
    except Exception as e:
        print(f"  [server] get_build: {e}")

    try:
        r = prov.get_runes(req.my_en_id, req.my_lane, opp_en)
        if r:
            runes_d = {
                "source": r.source, "stale": r.stale,
                "primary_tree":    _style_d(r.primary_tree),
                "secondary_tree":  _style_d(r.secondary_tree),
                "keystone":        _perk_d(r.keystone),
                "primary_perks":   [_perk_d(i) for i in r.primary_perks],
                "secondary_perks": [_perk_d(i) for i in r.secondary_perks],
                "stat_shards":     [_perk_d(i) for i in r.stat_shards],
            }
    except Exception as e:
        print(f"  [server] get_runes: {e}")

    try:
        if opp_en:
            m = prov.get_matchup(req.my_en_id, req.my_lane, opp_en)
            if m:
                matchup_d = {
                    "win_rate": m.win_rate, "sample_size": m.sample_size,
                    "stale": m.stale, "source": m.source,
                }
    except Exception as e:
        print(f"  [server] get_matchup: {e}")

    return {
        "opponent": {
            "en_id": opp_en, "zh_name": opp_zh,
            "avatar_url": _champ_icon(opp_en),
        } if opp_en else None,
        "matchup": matchup_d,
        "build":   build_d,
        "runes":   runes_d,
    }


# ── 接口：Claude 建议（慢）───────────────────────────────────────────────────

@app.post("/api/advise/tips")
def advise_tips(req: AdviseReq, _: None = Depends(_check_page_token)):
    prov    = _provider(req.tier)
    opp_en, opp_zh = _opponent(req)

    matchup_wr = None
    try:
        if opp_en:
            m = prov.get_matchup(req.my_en_id, req.my_lane, opp_en)
            if m:
                matchup_wr = m.win_rate
    except Exception:
        pass

    build_desc, runes_desc = _text_descs(req, opp_en, prov)

    enemy_members = []
    for e in req.enemy_team:
        info = dd.get_by_en_id(e.en_id) or dd.get_or_unknown(0)
        enemy_members.append(TeamMember(cell_id=-1, champ=info, position=e.lane))
    enemy_comp = analyze_enemy_comp(enemy_members, dd)
    comp_sum   = comp_summary_for_claude(enemy_comp)

    me_info = dd.get_by_en_id(req.my_en_id)
    me_zh   = me_info.zh_name if me_info else req.my_en_id

    my_team_list: list[tuple[str, str, str]] = []
    for ref in req.my_team:
        info = dd.get_by_en_id(ref.en_id)
        my_team_list.append((info.zh_name if info else ref.zh_name, ref.en_id, ref.lane))

    enemy_team_list: list[tuple[str, str, str]] = []
    for ref in req.enemy_team:
        info = dd.get_by_en_id(ref.en_id)
        enemy_team_list.append((info.zh_name if info else ref.zh_name, ref.en_id, ref.lane))

    lane_tips, teamfight, comp_adjust, tips_error = generate_tips(
        my_champ_zh=me_zh, my_champ_en=req.my_en_id,
        my_position_cn=POSITION_CN.get(req.my_lane, req.my_lane),
        opponent_zh=opp_zh, opponent_en=opp_en, opp_confidence="",
        my_team=my_team_list, enemy_team=enemy_team_list,
        build_desc=build_desc, runes_desc=runes_desc,
        matchup_wr=matchup_wr, comp_summary=comp_sum,
    )

    return {
        "lane_tips":   lane_tips,
        "teamfight":   teamfight,
        "comp_adjust": comp_adjust,
        "tips_error":  tips_error,
    }


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = "（鉴权已启用）" if PUSH_TOKEN else "（无鉴权，仅限本地开发）"
    print(f"[*] 启动服务…  http://localhost:8765  {mode}")
    uvicorn.run("server:app", host="0.0.0.0", port=8765, reload=False)
