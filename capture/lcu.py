"""
LCU (League Client Update) 连接辅助模块。

优先从 LeagueClientUx.exe 进程命令行参数读取端口与认证 token，
失败则回退到解析 lockfile。
"""

from __future__ import annotations

import re
import base64
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import httpx
import psutil

LOCKFILE_DEFAULT = Path(r"C:\Riot Games\League of Legends\lockfile")
PROCESS_NAME = "LeagueClientUx.exe"


@dataclass
class LCUCredentials:
    port: int
    token: str

    @property
    def base_url(self) -> str:
        return f"https://127.0.0.1:{self.port}"

    @property
    def auth_header(self) -> str:
        encoded = base64.b64encode(f"riot:{self.token}".encode()).decode()
        return f"Basic {encoded}"


def _creds_from_process() -> Optional[LCUCredentials]:
    """从 LeagueClientUx.exe 命令行参数读取 --app-port 和 --remoting-auth-token。"""
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            if proc.info["name"] != PROCESS_NAME:
                continue
            cmdline = " ".join(proc.info["cmdline"] or [])
            port_m  = re.search(r"--app-port=(\d+)", cmdline)
            token_m = re.search(r"--remoting-auth-token=([^\s\"]+)", cmdline)
            if port_m and token_m:
                return LCUCredentials(port=int(port_m.group(1)), token=token_m.group(1))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def _creds_from_lockfile(lockfile: Path = LOCKFILE_DEFAULT) -> Optional[LCUCredentials]:
    """解析 lockfile 格式：name:pid:port:token:protocol"""
    if not lockfile.exists():
        return None
    try:
        parts = lockfile.read_text(encoding="utf-8").strip().split(":")
        if len(parts) >= 5:
            return LCUCredentials(port=int(parts[2]), token=parts[3])
    except Exception as exc:
        print(f"[lcu] lockfile 解析失败: {exc}")
    return None


def get_credentials() -> LCUCredentials:
    """返回 LCU 认证信息，若客户端未运行则抛出 RuntimeError。"""
    creds = _creds_from_process()
    if creds:
        print(f"[lcu] 从进程参数读取认证  端口={creds.port}")
        return creds

    creds = _creds_from_lockfile()
    if creds:
        print(f"[lcu] 从 lockfile 读取认证  端口={creds.port}")
        return creds

    raise RuntimeError(
        "未找到英雄联盟客户端，请确认 LeagueClientUx.exe 正在运行。"
    )


def make_client(creds: LCUCredentials) -> httpx.Client:
    """返回预配置的 httpx.Client（关闭 SSL 校验，携带 Basic Auth）。"""
    return httpx.Client(
        base_url=creds.base_url,
        headers={"Authorization": creds.auth_header},
        verify=False,  # 本机自签名证书，仅限 127.0.0.1
        timeout=5.0,
    )


def fetch_champ_select_session(client: httpx.Client) -> dict | None:
    """
    GET /lol-champ-select/v1/session。
    未处于选人阶段（404）返回 None，其他 HTTP 错误抛出异常。
    """
    try:
        resp = client.get("/lol-champ-select/v1/session")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except httpx.RequestError as exc:
        raise RuntimeError(f"[lcu] 请求错误: {exc}") from exc
