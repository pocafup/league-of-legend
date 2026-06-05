# LOL 选人助手 — 交接文档

> 更新：2026-06-05（本 session 结束时写入）

---

## 项目部署信息

- **线上地址**：`https://league.pocafup.com/?demo=1&token=league-of-legend-gametoken`
- **服务器**：Docker + Caddy 反向代理 + Cloudflare CDN
- **构建**：`docker build -t lol-advisor . && docker compose up -d`
- **环境变量**（服务器上的 .env，**绝不提交 git**）：
  - `ANTHROPIC_API_KEY`：Anthropic API 密钥（已轮换，旧密钥因误提交被撤销）
  - `LEAGUE_PUSH_TOKEN`：页面/推送鉴权 token（`league-of-legend-gametoken`）
- **本机 agent**：`uv run python agent.py`（读 LCU → POST /api/session 到服务器）

---

## 已完成功能（MVP 全部完成）

### 核心流程
1. `agent.py` 监听本机 LCU，选人完成后推送到服务器
2. `server.py` FastAPI 后端，提供：
   - `GET /api/draft`：返回当前阵容
   - `GET /api/demo`：内置演示数据（雷克顿 vs 诺手阵容）
   - `POST /api/advise/build`：出装/符文/阵容合成（无 Claude，快）
   - `POST /api/advise/tips`：对线注意点 + 团战分析（Claude，慢）
3. 前端 `web/index.html` + `web/app.js`：两步异步加载（先出装后 tips）

### 数据来源
- **Lolalytics**（`stats/lolalytics.py`）：出装、符文、对位胜率
  - 本地缓存（`cache/stats/`），key = 版本+英雄+位置
  - 缓存未命中时实时抓取；失败时用旧缓存并标注过期
- **DDragon**（`data/ddragon.py`）：英雄名双向映射、装备名/图标、符文名/图标、技能名

---

## 本 Session 最重要的新增：多对位阵容合成出装系统

### 问题
之前只查"我 vs 对线对手"的单对位数据，item3-5 完全由对线对手决定，忽略了全队构成。

### 解决方案
**`advisor/comp_build.py`** — 核心引擎：

1. 拉取"我 vs 每个敌方英雄"共5张装备胜率表（Lolalytics `get_item_table`）
2. 按位置分配权重：对线路=0.35，打野=0.20，其他=0.15
3. **item1-2（对线期）** 只用对线对手的数据（1v1 阶段）
4. **item3-5（后期）** 多对位加权混合：`data_score = Σ weight × (wr/100) × (pick/100)`
5. 叠加阵容加分（`config/comp_scoring.json` 配置）：
   - 敌方魔法伤害 ≥ 55% → 魔抗装 +25%
   - 敌方物理伤害 ≥ 55% → 护甲装 +20%
   - 硬控 ≥ 3 → 韧性装 +25%
   - 坦克 ≥ 2 → 破甲装 +25%
   - 回复 ≥ 2 → 斩铁装（指定 ID 列表）+30%
6. 每件装备带 `reasons` 列表（数据分明细 + 阵容加分说明）

**相关文件变更：**
- `config/comp_scoring.json`：所有阈值和权重外部化，无需改代码
- `advisor/comp_adjust.py`：新增 `phys_pct`/`magic_pct` 属性、`to_profile_dict()` 方法
- `data/ddragon.py`：新增 `item_tags(item_id) -> list[str]`（读 DDragon tags，用于护甲/魔抗等判断）
- `stats/lolalytics.py`：新增 `_parse_item_tables()` 解析各槽装备排名表；`get_item_table()` 返回单对位完整装备表，样本 < 200 时返回 None（不用低质量数据混合）

### API 变更
`POST /api/advise/build` 现在额外返回 `comp_build` 字段：
```json
{
  "comp_build": {
    "starter":      [{"id": ..., "name": ..., "icon_url": ...}],
    "boots":        [...],
    "early_items":  [{"id": ..., "name": ..., "icon_url": ..., "score": 0.12, "data_score": 0.09, "comp_bonus": 0.03, "reasons": ["..."]}],
    "late_items":   [...],
    "comp_profile": {"phys_pct": 0.6, "magic_pct": 0.4, "tank_count": 1, "hard_cc_count": 2, "healer_count": 1, ...},
    "sources":      ["vs 诺手（权重35%，12430局）", "..."],
    "warnings":     ["vs XXX 样本不足，未纳入混合"]
  }
}
```

### 前端变更（`web/index.html` + `web/app.js`）
- `_renderBuild(data)`：优先使用 `comp_build`，`comp_build` 不存在时回退到旧的单对位 `build`
- `_compProfileStrip(profile)`：在出装区块上方显示彩色标签条（物理/法系比例、硬控数、坦克数、回复数）
- `_scoredItemsRow(label, items)`：渲染带 hover 原因 tooltip 的装备（多行，有阵容加分的装备显示绿色边框）
- 新增 CSS：`.comp-profile-strip`、`.comp-tag.*`、`.item-reason-tooltip`、`.reason-line`

---

## Claude 提示词改进（本 Session）

问题根源：Claude 不知道对手的出装/技能，会从训练数据里幻想。

修复（`advisor/tips.py`，`generate_matchup_tips`）：
- 传入 `vs_build_desc`、`vs_runes_desc`：对手当前版本出装和符文（来自统计站）
- 传入 `me_spells`、`vs_spells`：双方技能名（Q/W/E/R/被动，来自 DDragon）
- 提示词约束：「技能效果细节不在提供范围内，如不确定请只提技能名，不要描述效果」
- 模型升级：haiku → `claude-sonnet-4-6`

---

## 待完成 / 可优化项

1. **验证测试**（未做）：用 demo 数据，把敌方中路从泽德改为辛德拉，确认后期装备 item3-5 发生变化（应出现魔抗装）。需要修改 `server.py` 的 `_DEMO_SESSION` 做 A/B 对比。

2. **Claude 获得 comp_build 上下文**（未做）：当前 `/api/advise/tips` 传给 Claude 的出装描述仍是单对位文本；理想情况是把 `comp_build.late_items` 的 reasons 也传给 Claude，让 `comp_adjust` 这一栏内容更精准。实现方式：在 tips 端点里也调用 `build_comp_build()`（Lolalytics 有缓存，不会慢），生成文本摘要后附在 prompt 里。

3. **GW 装备 ID 维护**（`config/comp_scoring.json` 的 `gw_item_ids`）：版本更新后斩铁类装备 ID 可能变，需要手动同步。

4. **主要测试入口**：`https://league.pocafup.com/?demo=1&token=league-of-legend-gametoken`

---

## 架构概览

```
agent.py (本机)
  └─ 读 LCU lockfile → 轮询 /lol-champ-select/v1/session
  └─ POST /api/session → server.py

server.py (服务端 Docker)
  ├─ /api/draft        ← 返回 agent 推上来的阵容
  ├─ /api/demo         ← 内置演示数据（无需 agent）
  ├─ /api/advise/build ← 出装/符文/comp_build（快）
  └─ /api/advise/tips  ← Claude tips（慢，~3-5s）

数据依赖：
  DDragonClient (data/ddragon.py)     ← 启动时加载，缓存静态数据
  LolalyticsProvider (stats/)         ← 按需抓取，本地缓存
  CompBuildEngine (advisor/comp_build.py) ← 多对位混合 + 阵容加分
  analyze_enemy_comp (advisor/comp_adjust.py) ← 阵容档案分析
  generate_tips (advisor/tips.py)     ← Claude sonnet-4-6
```

---

## 安全注意事项

- `ANTHROPIC_API_KEY` 曾因误提交 `.env~` 备份文件被 GitHub secret scanning 拦截，密钥已轮换。
- `.gitignore` 已添加 `*.env*`、`*~`、`*.un~`
- 服务器上 `.env` 文件通过 `docker compose` 注入，绝不进 git

---

## 快速验证清单

```bash
# 1. 确认 Docker 在跑
docker ps | grep lol

# 2. 确认 API 可用
curl -s "https://league.pocafup.com/api/demo?token=league-of-legend-gametoken" | python3 -m json.tool | head -20

# 3. 确认 comp_build 返回
curl -s -X POST "https://league.pocafup.com/api/advise/build?token=league-of-legend-gametoken" \
  -H "Content-Type: application/json" \
  -d '{"my_en_id":"Renekton","my_lane":"top","my_team":[{"en_id":"Renekton","zh_name":"雷克顿","lane":"top"}],"enemy_team":[{"en_id":"Darius","zh_name":"德莱厄斯","lane":"top"},{"en_id":"LeeSin","zh_name":"李青","lane":"jungle"},{"en_id":"Zed","zh_name":"泽德","lane":"middle"},{"en_id":"Caitlyn","zh_name":"凯特琳","lane":"bottom"},{"en_id":"Lulu","zh_name":"璐璐","lane":"utility"}],"tier":"emerald_plus"}' \
  | python3 -m json.tool | grep -A5 '"comp_build"'
```
