"""
调用 Claude 生成对线注意点 + 团战分析。
这是本项目唯一使用 LLM 的地方。

读取环境变量 ANTHROPIC_API_KEY；未设置时抛出 RuntimeError（调用方捕获后降级）。
"""
from __future__ import annotations

import json
import os
from typing import Optional

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024

# 如有失败返回的占位文本
_FAIL_TIPS = "(生成失败，稍后重试)"


def _build_prompt(
    my_champ_zh: str,
    my_champ_en: str,
    my_position_cn: str,
    opponent_zh: Optional[str],
    opponent_en: str,
    opp_confidence: str,
    my_team: list[tuple[str, str, str]],    # [(zh_name, en_id, pos_cn)]
    enemy_team: list[tuple[str, str, str]], # [(zh_name, en_id, pos_cn)]
    build_desc: str,
    runes_desc: str,
    matchup_wr: Optional[float],
    comp_summary: str = "",
) -> str:
    if opponent_zh and opponent_en and opponent_en != "Unknown":
        opp_line = f"{opponent_zh}（{opponent_en}，{opp_confidence}）"
    elif opponent_zh:
        opp_line = f"{opponent_zh}（{opp_confidence}）"
    else:
        opp_line = "未知"
    wr_line = (
        f"对位胜率：{matchup_wr:.1f}%"
        if matchup_wr is not None else "对位胜率：暂无数据"
    )
    my_lines  = "\n".join(f"  {pos}: {zh}({en})" for zh, en, pos in my_team)
    opp_lines = "\n".join(f"  {pos}: {zh}({en})（推断）" for zh, en, pos in enemy_team)
    comp_section = f"\n{comp_summary}" if comp_summary else ""

    return f"""你是英雄联盟教练助手。基于以下选人数据给出针对性建议。要求：简短具体（提到英雄名/技能/装备），不写泛泛套话，全程中文。

## 本局数据
我的英雄：{my_champ_zh}（{my_champ_en}） / {my_position_cn}
对线对手：{opp_line}
{wr_line}

我方阵容：
{my_lines}

敌方阵容：
{opp_lines}
{comp_section}

出装与符文（对位数据）：
{build_desc}
{runes_desc}

## 输出要求
严格只输出下面的 JSON，不要任何其他文字：
{{
  "lane_tips": [
    "3-5条，针对此对位：对手强势期/弱势期、要躲的关键技能、何时换血或避战、核心装成型节点"
  ],
  "teamfight": [
    "2-4条：我方开/接团方式、集火谁、躲对面哪个关键技能、站位与进场时机"
  ],
  "comp_adjust": [
    "2-4条，针对敌方阵容的具体出装调整建议。格式：因为对面有XXX→考虑把YYY换成ZZZ / 在第N件加一件ZZZ。只在有实质调整时写，没有就给空数组"
  ]
}}"""


def generate_tips(
    my_champ_zh: str,
    my_champ_en: str,
    my_position_cn: str,
    opponent_zh: Optional[str],
    opponent_en: str = "",
    opp_confidence: str = "",
    my_team: list[tuple[str, str, str]] = [],
    enemy_team: list[tuple[str, str, str]] = [],
    build_desc: str = "",
    runes_desc: str = "",
    matchup_wr: Optional[float] = None,
    comp_summary: str = "",
) -> tuple[list[str], list[str], list[str], str]:
    """
    返回 (lane_tips, teamfight, comp_adjust, error_msg)。
    error_msg 为空字符串表示成功；非空时所有列表均为 [_FAIL_TIPS]。
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        msg = "未设置 ANTHROPIC_API_KEY，跳过 AI 建议"
        print(f"  [tips] {msg}")
        return [_FAIL_TIPS], [_FAIL_TIPS], [], msg

    try:
        import anthropic  # lazy import，避免启动时阻塞
    except ImportError:
        msg = "anthropic 包未安装，请执行 uv add anthropic"
        print(f"  [tips] {msg}")
        return [_FAIL_TIPS], [_FAIL_TIPS], [], msg

    prompt = _build_prompt(
        my_champ_zh, my_champ_en, my_position_cn,
        opponent_zh, opponent_en, opp_confidence,
        my_team, enemy_team,
        build_desc, runes_desc, matchup_wr,
        comp_summary,
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = resp.content[0].text.strip()
    except Exception as e:
        msg = f"Claude API 调用失败: {e}"
        print(f"  [tips] {msg}")
        return [_FAIL_TIPS], [_FAIL_TIPS], [], msg

    # 解析 JSON
    try:
        clean = raw_text
        if "```" in clean:
            parts = clean.split("```")
            for p in parts:
                p = p.strip()
                if p.startswith("json"):
                    p = p[4:].strip()
                if p.startswith("{"):
                    clean = p
                    break
        data = json.loads(clean)
        lane_tips    = [str(t) for t in data.get("lane_tips",    []) if t]
        teamfight    = [str(t) for t in data.get("teamfight",    []) if t]
        comp_adjust  = [str(t) for t in data.get("comp_adjust",  []) if t]
        if not lane_tips:
            lane_tips = [_FAIL_TIPS]
        if not teamfight:
            teamfight = [_FAIL_TIPS]
        return lane_tips, teamfight, comp_adjust, ""
    except Exception as e:
        msg = f"JSON 解析失败: {e}（原始输出前200字: {raw_text[:200]}）"
        print(f"  [tips] {msg}")
        return [_FAIL_TIPS], [_FAIL_TIPS], [], msg


def generate_matchup_tips(
    me_zh: str,
    me_en: str,
    lane_cn: str,
    vs_zh: str,
    vs_en: str = "",
    build_desc: str = "",
    runes_desc: str = "",
    matchup_wr: Optional[float] = None,
) -> tuple[list[str], str]:
    """
    生成 3-6 条对线注意点（仅对线，不含团战/阵容部分）。
    返回 (tips, error_msg)。error_msg 为空表示成功。
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        msg = "未设置 ANTHROPIC_API_KEY，跳过 AI 建议"
        print(f"  [tips] {msg}")
        return [_FAIL_TIPS], msg

    try:
        import anthropic
    except ImportError:
        msg = "anthropic 包未安装，请执行 uv add anthropic"
        print(f"  [tips] {msg}")
        return [_FAIL_TIPS], msg

    wr_line = (
        f"对位胜率：{matchup_wr:.1f}%"
        if matchup_wr is not None else "对位胜率：暂无数据"
    )

    vs_label = f"{vs_zh}（{vs_en}）" if vs_en and vs_en != "Unknown" else vs_zh
    prompt = f"""你是英雄联盟教练助手。基于以下对位数据给出针对性对线建议。
要求：简短具体（提到技能名/装备名），不写泛泛套话，全程中文。

## 对位数据
我的英雄：{me_zh}（{me_en}） / {lane_cn}
对手：{vs_label}
{wr_line}

出装与符文（对位数据）：
{build_desc}
{runes_desc}

## 输出要求
严格只输出下面的 JSON，不要任何其他文字：
{{"tips": [
  "3-6条，针对此对位的具体对线思路：对手强势/弱势期、需注意的关键技能、换血时机、核心装备成型节点"
]}}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = resp.content[0].text.strip()
    except Exception as e:
        msg = f"Claude API 调用失败: {e}"
        print(f"  [tips] {msg}")
        return [_FAIL_TIPS], msg

    try:
        clean = raw_text
        if "```" in clean:
            for p in clean.split("```"):
                p = p.strip()
                if p.startswith("json"):
                    p = p[4:].strip()
                if p.startswith("{"):
                    clean = p
                    break
        data = json.loads(clean)
        tips = [str(t) for t in data.get("tips", []) if t]
        if not tips:
            tips = [_FAIL_TIPS]
        return tips, ""
    except Exception as e:
        msg = f"JSON 解析失败: {e}（原始输出前200字: {raw_text[:200]}）"
        print(f"  [tips] {msg}")
        return [_FAIL_TIPS], msg
