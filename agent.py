"""
LOL 选人助手 — 本机采集 Agent

从本地英雄联盟客户端轮询选人会话，有变化时 POST 到服务端。

配置（优先读 .env，其次环境变量）：
  SERVER_URL   = http://localhost:8765   (服务端地址)
  PUSH_TOKEN   = ""                      (与服务端 LEAGUE_PUSH_TOKEN 一致；空=不鉴权)
  POLL_INTERVAL = 1.5                    (轮询间隔，秒)

启动: uv run python agent.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import httpx

SERVER_URL    = os.environ.get("SERVER_URL", "http://localhost:8765").rstrip("/")
PUSH_TOKEN    = os.environ.get("PUSH_TOKEN", os.environ.get("LEAGUE_PUSH_TOKEN", ""))
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "1.5"))

# 推送失败后指数退避上限（秒）
_MAX_RETRY_DELAY = 30.0


def _push_session(raw: dict) -> bool:
    """POST /api/session。成功返回 True，失败返回 False（调用方重试）。"""
    headers: dict[str, str] = {}
    if PUSH_TOKEN:
        headers["X-Push-Token"] = PUSH_TOKEN
    try:
        resp = httpx.post(
            f"{SERVER_URL}/api/session",
            json={"raw": raw},
            headers=headers,
            timeout=8.0,
        )
        resp.raise_for_status()
        return True
    except httpx.HTTPStatusError as e:
        print(f"  [agent] 推送失败 HTTP {e.response.status_code}: {e.response.text[:120]}")
        return False
    except Exception as e:
        print(f"  [agent] 推送失败: {e}")
        return False


def _session_hash(raw: dict | None) -> int:
    if raw is None:
        return 0
    return hash(json.dumps(raw, ensure_ascii=False, sort_keys=True))


def run() -> None:
    from capture.lcu import get_credentials, make_client, fetch_champ_select_session

    print(f"[agent] 目标服务端: {SERVER_URL}")
    print(f"[agent] 鉴权: {'已启用' if PUSH_TOKEN else '未启用（开发模式）'}")
    print(f"[agent] 轮询间隔: {POLL_INTERVAL}s")
    print("[agent] 连接本地英雄联盟客户端…")

    try:
        creds = get_credentials()
    except RuntimeError as e:
        print(f"[agent] 错误: {e}")
        sys.exit(1)

    print(f"[agent] 已连接 LCU → {creds.base_url}")
    print("[agent] 开始监听选人（Ctrl+C 退出）\n")

    last_hash: int = 0
    retry_delay: float = 2.0
    in_lobby: bool = False

    with make_client(creds) as lcu:
        while True:
            try:
                raw = fetch_champ_select_session(lcu)

                if raw is None:
                    if in_lobby:
                        print("\n[agent] 已离开选人室")
                        in_lobby = False
                        last_hash = 0
                    else:
                        print(".", end="", flush=True)
                else:
                    in_lobby = True
                    h = _session_hash(raw)
                    if h != last_hash:
                        last_hash = h
                        picked = sum(
                            1 for a in raw.get("actions", [[]])[0]
                            if a.get("completed") and a.get("championId", 0)
                        )
                        print(f"\r[agent] 检测到变化，推送阵容（已选 {picked} 人）…", end="", flush=True)
                        ok = _push_session(raw)
                        if ok:
                            retry_delay = 2.0
                            print(" ✓")
                        else:
                            print(f"\n[agent] 将在 {retry_delay:.0f}s 后重试…")
                            time.sleep(retry_delay)
                            retry_delay = min(retry_delay * 2, _MAX_RETRY_DELAY)
                            last_hash = 0  # 强制下次重推

                time.sleep(POLL_INTERVAL)

            except RuntimeError as e:
                print(f"\n[agent] LCU 错误: {e}")
                time.sleep(3.0)
            except Exception as e:
                print(f"\n[agent] 未预期错误: {e}")
                time.sleep(3.0)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\n[agent] 已停止。")
