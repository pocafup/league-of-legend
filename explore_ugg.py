"""
一次性探针：尝试多个 U.GG URL 模板，找到能返回 200 的那条，
把原始 JSON dump 到 cache/ugg_raw/ 供人工检查。
"""
import json, sys
from pathlib import Path
import httpx

CACHE = Path("cache/ugg_raw")
CACHE.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://u.gg/",
    "Accept": "application/json, text/plain, */*",
}

# Gangplank(41), 用于探测；版本从 DDragon 16.11.1 推出几种可能格式
CHAMP_ID = 41
PATCHES  = ["16_11", "16_11_1", "16.11", "16.11.1"]
QUEUES   = ["ranked_solo_5x5", "420", "ranked"]
RANKS    = ["11", "10", "12", "overall", "platinum_plus"]

BASE = "https://stats2.u.gg/lol"
VERSIONS = ["1.5", "1.1", "2.0"]

candidates = []
for ver in VERSIONS:
    for patch in PATCHES:
        for queue in QUEUES:
            for rank in RANKS:
                candidates.append(
                    f"{BASE}/{ver}/overview/{patch}/{queue}/{CHAMP_ID}/{rank}.json"
                )

print(f"共 {len(candidates)} 条候选 URL，开始探测（遇到 200 立即停止）…\n")

found = None
with httpx.Client(timeout=10, headers=HEADERS, follow_redirects=True) as client:
    for url in candidates:
        try:
            r = client.get(url)
            status = r.status_code
            size   = len(r.content)
            print(f"  {status}  {size:>8} bytes  {url}")
            if status == 200 and size > 500:
                found = (url, r)
                break
        except Exception as e:
            print(f"  ERR  {url}  → {e}")

if not found:
    print("\n所有候选均失败，需要换思路。")
    sys.exit(1)

url, resp = found
data = resp.json()
out  = CACHE / f"ugg_raw_{CHAMP_ID}.json"
out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n✓ 命中: {url}")
print(f"  已保存 → {out}")

# 打印顶层结构，方便快速判断
print("\n── 顶层结构 ──")
if isinstance(data, dict):
    for k, v in data.items():
        snippet = json.dumps(v, ensure_ascii=False)[:120]
        print(f"  {k!r}: {snippet}")
elif isinstance(data, list):
    print(f"  列表，长度 {len(data)}，前两项类型: {[type(x).__name__ for x in data[:2]]}")
    for i, item in enumerate(data[:2]):
        print(f"  [{i}]: {json.dumps(item, ensure_ascii=False)[:200]}")
