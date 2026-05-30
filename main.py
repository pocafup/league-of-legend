"""
LOL 选人助手 — 主入口
  live 模式（默认）:  uv run main.py
  回放模式:           uv run main.py --replay fixtures/sample_5v5.json
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()   # 读取项目根目录的 .env（若存在）

import httpx

from advisor.assemble import assemble_advice
from advisor.session import DraftSession, enrich_session, parse_lcu_session
from capture.lcu import fetch_champ_select_session, get_credentials, make_client
from data.ddragon import DDragonClient
from render.terminal import render_advice
from stats.lolalytics import LolalyticsProvider
from stats.opgg import OpggProvider

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
LAST_SESSION_FILE = CACHE_DIR / "last_session.json"

# ── 对位查询：分路/段位标准化 ─────────────────────────────────────────────────

_LANE_ALIASES: dict[str, str] = {
    "top": "top",        "上": "top",   "上单": "top",
    "jungle": "jungle",  "jg": "jungle", "打野": "jungle", "野": "jungle",
    "mid": "middle",     "middle": "middle", "中": "middle", "中单": "middle",
    "bot": "bottom",     "bottom": "bottom", "adc": "bottom",
    "下": "bottom",      "下路": "bottom",
    "sup": "utility",    "support": "utility", "supp": "utility",
    "辅助": "utility",   "辅": "utility",
}

_TIER_ALIASES: dict[str, str] = {
    "emerald+": "emerald_plus", "emerald_plus": "emerald_plus",
    "翡翠+": "emerald_plus", "e+": "emerald_plus",
    "diamond+": "diamond_plus", "diamond_plus": "diamond_plus",
    "钻石+": "diamond_plus", "d+": "diamond_plus",
    "master+": "master_plus", "master_plus": "master_plus", "master": "master_plus",
    "大师+": "master_plus", "m+": "master_plus",
    "platinum+": "platinum_plus", "platinum_plus": "platinum_plus",
    "铂金+": "platinum_plus", "p+": "platinum_plus",
    "gold+": "gold_plus", "gold_plus": "gold_plus",
    "黄金+": "gold_plus", "g+": "gold_plus",
    "all": "all", "全段位": "all",
}

_LANE_CN: dict[str, str] = {
    "top": "上单", "jungle": "打野", "middle": "中路",
    "bottom": "下路", "utility": "辅助",
}


# ── 对位手动查询 ─────────────────────────────────────────────────────────────

def run_matchup(
    me_query: str,
    vs_query: str,
    lane_input: str,
    tier_input: str,
    dd: DDragonClient,
    version: str,
) -> None:
    # 1. 分路
    lane = _LANE_ALIASES.get(lane_input.lower().strip())
    if lane is None:
        print(f"[!] 未识别的分路: '{lane_input}'")
        print("    支持: top/上单  jungle/打野  mid/中单  bot/下路  sup/辅助")
        sys.exit(1)
    lane_cn = _LANE_CN[lane]

    # 2. 段位
    tier = _TIER_ALIASES.get(tier_input.lower().strip(), tier_input.lower().strip())

    # 3. 英雄解析
    def _resolve(query: str, flag: str):
        hits = dd.find_champ(query)
        if not hits:
            print(f"[!] 找不到英雄: '{query}'  (支持中文名/英文 ID/常用昵称)")
            sys.exit(1)
        if len(hits) == 1:
            return hits[0]
        print(f"[?] '{query}' ({flag}) 匹配到多个英雄，请更精确地指定:")
        for idx, c in enumerate(hits[:5], 1):
            print(f"   {idx}. {c.zh_name} ({c.en_id})")
        sys.exit(1)

    me = _resolve(me_query, "--me")
    vs = _resolve(vs_query, "--vs")
    print(f"\n[*] 查询: {me.zh_name}({me.en_id}) vs {vs.zh_name}({vs.en_id}) / {lane_cn} / {tier}")

    # 4. 获取统计数据
    provider = LolalyticsProvider(tier=tier)
    build = runes = matchup = None
    try:
        build   = provider.get_build(me.en_id, lane, vs.en_id)
    except Exception as e:
        print(f"  [matchup] get_build 失败: {e}")
    try:
        runes   = provider.get_runes(me.en_id, lane, vs.en_id)
    except Exception as e:
        print(f"  [matchup] get_runes 失败: {e}")
    try:
        matchup = provider.get_matchup(me.en_id, lane, vs.en_id)
    except Exception as e:
        print(f"  [matchup] get_matchup 失败: {e}")

    # 5. 构建 Claude 描述字串
    bp: list[str] = []
    if build:
        if build.starter:
            bp.append("起手：" + " → ".join(dd.item_name(i) for i in build.starter))
        if build.boots:
            bp.append("鞋子：" + " → ".join(dd.item_name(i) for i in build.boots))
        if build.core:
            bp.append("核心：" + " → ".join(dd.item_name(i) for i in build.core))
        sit = build.fourth + build.fifth + build.sixth
        if sit:
            bp.append("按需：" + " / ".join(dd.item_name(i) for i in sit))
    build_desc = "出装：" + "；".join(bp) if bp else "出装：暂无数据"

    runes_desc = "符文：暂无数据"
    if runes:
        pri = dd.rune_style_name(runes.primary_tree)
        ks  = dd.rune_name(runes.keystone) if runes.keystone else "无"
        pp  = " / ".join(dd.rune_name(i) for i in runes.primary_perks) if runes.primary_perks else "无"
        sec = dd.rune_style_name(runes.secondary_tree)
        sp  = " / ".join(dd.rune_name(i) for i in runes.secondary_perks) if runes.secondary_perks else "无"
        runes_desc = f"符文：主系{pri}（{ks}），{pp}；副系{sec}，{sp}"

    # 6. Claude tips
    from advisor.tips import generate_matchup_tips
    print("  [matchup] 调用 Claude 生成对线建议…")
    lane_tips, tips_error = generate_matchup_tips(
        me_zh=me.zh_name, me_en=me.en_id,
        lane_cn=lane_cn, vs_zh=vs.zh_name, vs_en=vs.en_id,
        build_desc=build_desc, runes_desc=runes_desc,
        matchup_wr=matchup.win_rate if matchup else None,
    )

    # 7. 渲染
    from render.terminal import render_matchup
    render_matchup(
        me_zh=me.zh_name, me_en=me.en_id,
        vs_zh=vs.zh_name, vs_en=vs.en_id,
        lane_cn=lane_cn, tier=tier, version=version,
        matchup=matchup, build=build, runes=runes,
        lane_tips=lane_tips, tips_error=tips_error,
        dd=dd,
    )


# ── 选人完成判断 ─────────────────────────────────────────────────────────────

def _is_all_picked(ds: DraftSession) -> bool:
    """判断选人是否完成，可生成建议。"""
    my_done = (
        len(ds.my_team) == 5
        and all(m.champ.numeric_id != 0 for m in ds.my_team)
    )

    # 自定义/人机对局：敌方机器人 championId 始终为 0，不等敌方。
    # 只要我方 5 人全部确定（本地玩家 + 友方机器人）即触发。
    if ds.is_custom:
        return my_done

    return my_done and (
        len(ds.enemy_team) == 5
        and all(m.champ.numeric_id != 0 for m in ds.enemy_team)
    )


# ── 完整流水线 ───────────────────────────────────────────────────────────────

def run_pipeline(
    raw: dict,
    dd: DDragonClient,
    provider: OpggProvider,
    version: str,
) -> None:
    """解析 → 推断 → 查数据 → 生成建议 → 打印。"""
    ds = parse_lcu_session(raw, dd)
    enrich_session(ds)
    advice = assemble_advice(ds, provider, dd, version)
    render_advice(advice, dd)


# ── R 键监听（后台线程）──────────────────────────────────────────────────────

def _start_refresh_listener() -> threading.Event:
    """
    后台线程等待用户输入 'r' + Enter，触发后设置 Event。
    用 daemon=True，主线程退出时自动终止。
    """
    ev = threading.Event()

    def _worker() -> None:
        while True:
            try:
                line = input()
                if line.strip().lower() == "r":
                    ev.set()
            except (EOFError, KeyboardInterrupt):
                break

    threading.Thread(target=_worker, daemon=True).start()
    return ev


# ── 回放模式 ─────────────────────────────────────────────────────────────────

def run_replay(
    session_path: Path,
    dd: DDragonClient,
    provider: OpggProvider,
    version: str,
) -> None:
    print(f"[回放] 读取 {session_path}")
    raw = json.loads(session_path.read_text(encoding="utf-8"))
    run_pipeline(raw, dd, provider, version)


# ── live 模式 ────────────────────────────────────────────────────────────────

def run_live(
    dd: DDragonClient,
    provider: OpggProvider,
    version: str,
) -> None:
    print("[live] 连接英雄联盟客户端…")
    try:
        creds = get_credentials()
    except RuntimeError as exc:
        print(f"[!] {exc}")
        sys.exit(1)

    refresh_ev = _start_refresh_listener()
    last_session_hash: int | None = None
    last_advice_hash:  int | None = None   # 上次生成建议时的 session hash
    in_lobby = False                       # 是否处于选人室

    with make_client(creds) as client:
        print(f"[live] 已连接 → {creds.base_url}")
        print("[live] 等待选人完成（输入 r + Enter 可重新查询，Ctrl+C 退出）\n")

        while True:
            try:
                raw = fetch_champ_select_session(client)

                if raw is None:
                    # 不在选人室
                    if in_lobby:
                        print("\n[live] 已离开选人室，等待下一局…\n")
                        in_lobby = False
                        last_session_hash = None
                        last_advice_hash  = None
                    else:
                        print(".", end="", flush=True)

                else:
                    in_lobby = True
                    h = hash(json.dumps(raw, ensure_ascii=False, sort_keys=True))
                    force = refresh_ev.is_set()
                    if force:
                        refresh_ev.clear()
                        print("\n[!] 手动重新查询…")

                    if h != last_session_hash or force:
                        last_session_hash = h
                        LAST_SESSION_FILE.write_text(
                            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                        ds = parse_lcu_session(raw, dd)

                        if _is_all_picked(ds):
                            if last_advice_hash != h or force:
                                last_advice_hash = h
                                print(f"\n[live] 双方选人完成，生成建议中…")
                                run_pipeline(raw, dd, provider, version)
                        else:
                            picked = sum(
                                1 for m in ds.my_team + ds.enemy_team
                                if m.champ.numeric_id != 0
                            )
                            print(f"\r[live] 选人中… 已选 {picked}/10", end="", flush=True)

            except RuntimeError as exc:
                print(f"\n[!] {exc}")
            except httpx.HTTPStatusError as exc:
                print(f"\n[!] HTTP {exc.response.status_code}: {exc.request.url}")
            except Exception as exc:
                print(f"\n[!] 未预期错误: {exc}")

            time.sleep(1.0)


# ── 入口 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="LOL 选人助手")
    parser.add_argument("--replay", metavar="SESSION_JSON", help="回放模式：指定 session JSON 文件")

    sub = parser.add_subparsers(dest="command")
    mp = sub.add_parser("matchup", help="手动查询对位数据（不经过 LCU）")
    mp.add_argument("--me",   required=True, metavar="CHAMP", help="我的英雄（中文名/英文ID/昵称）")
    mp.add_argument("--vs",   required=True, metavar="CHAMP", help="对手英雄（中文名/英文ID/昵称）")
    mp.add_argument("--lane", required=True, metavar="LANE",  help="分路 (top/jungle/mid/bot/sup 或中文)")
    mp.add_argument("--tier", default="emerald_plus", metavar="TIER", help="段位 (默认: emerald_plus)")

    args = parser.parse_args()

    # 初始化 DDragon（含英雄/装备/符文翻译）
    dd = DDragonClient(cache_dir=CACHE_DIR)
    print("[*] 加载 Data Dragon…")
    try:
        version = dd.ensure_loaded()
        print(f"[*] 版本 {version} 就绪\n")
    except Exception as exc:
        print(f"[!] DDragon 加载失败: {exc}")
        sys.exit(1)

    if args.command == "matchup":
        run_matchup(args.me, args.vs, args.lane, args.tier, dd, version)
        return

    provider = LolalyticsProvider(tier="emerald_plus")

    if args.replay:
        run_replay(Path(args.replay), dd, provider, version)
    else:
        run_live(dd, provider, version)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[*] 已停止。")
