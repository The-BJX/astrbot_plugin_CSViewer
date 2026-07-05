# CS Pro League API

从完美世界CS电竞数据中心 (https://data.wanmei.com/csgo) 获取CS职业比赛信息的命令行工具。

## 部署

### 环境要求

- Python 3.8+
- Windows / Linux / macOS
- 网络连接（需可访问 `data.wanmei.com` 和 `esports.wanmei.com`）

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/your-username/CSProLeagueAPI.git
cd CSProLeagueAPI

# 2. 安装依赖
pip install -r requirements.txt

# 3. 初始化 API 配置
# 方式 A: 使用预置模板（推荐，无需浏览器）
#    apis.json 不存在时会自动从 apis.json.example 创建
python fetcher.py -e match -p

# 方式 B: 运行 API 发现工具（需要 Playwright 浏览器）
pip install playwright
python -m playwright install chromium
python discover.py
```

### 验证安装

```bash
# 查看今天比赛（如报错请检查网络和 apis.json）
python fetcher.py -e match -p

# 查看战队排名
python fetcher.py -e team -p

# 查看选手详情
python fetcher.py -e player --player-name donk -p
```

### 更新 API 配置

如果 API 端点变更或认证失效：

```bash
python discover.py   # 重新发现端点（需 Playwright）
```

> `apis.json` 已加入 `.gitignore`，每次运行 `discover.py` 会重新生成。
> 如需恢复默认配置，删除 `apis.json` 后自动从 `apis.json.example` 重建。

## 命令参考

### `-e` / `--endpoint` — 数据查询

按关键词匹配 API 端点查询数据：

| 关键词 | 说明 | 数据来源 |
|--------|------|----------|
| `match` | 比赛列表 / 详情 / 历史 | `getMatchList`, `getMatchDetail` |
| `team` | 战队HLTV排名 | `team/rank` |
| `event` | 赛事列表 / 赛程 | `event/getEventList`, `getMatchList`(扫描) |
| `player` | 选手详情 / MVP排行 | `player/stat/detail`, `player/mvp/top` |
| `transfer` | 选手转会 | `player/transfer` |

### `-p` / `--pretty` — 人类友好输出

隐藏ID/URL等技术字段，显示可读性高的内容。

```bash
# 查看今天比赛
python fetcher.py -e match -p

# 查看战队排名（含队员名单）
python fetcher.py -e team -p
```

### `--recent N` — 最近已结束比赛

扫描最近 N 场已结束比赛（逆向逐日扫描）：

```bash
# 最近10场已结束比赛
python fetcher.py -e match -p --recent 10
```

可搭配 `--team` 筛选特定队伍的比赛（使用 `team/recent/matches` 端点，速度更快）：

```bash
# FaZe 最近5场
python fetcher.py -e match -p --recent 5 --team FaZe
```

### `--team` / `--team-id` — 按队伍筛选

在比赛列表中筛选特定队伍的比赛：

```bash
# 按名称模糊匹配
python fetcher.py -e match -p --team FaZe

# 按ID精确匹配
python fetcher.py -e match -p --team-id 3430

# 查看某队伍历史已结束比赛
python fetcher.py -e match -p -d 2026-07-01 --status 3 --team FaZe
```

### `--player-name` / `-i` — 选手查询

按游戏名或ID查询选手详细档案：

```bash
# 按游戏名查询（模糊匹配，大小写敏感区分同名选手）
python fetcher.py -e player --player-name donk -p

# 按ID精确查询
python fetcher.py -e player -i 21167 -p
```

游戏名解析使用 `match/fuzzySearch` 端点，优先精确匹配（大小写敏感），
以此区分同名选手（如 `niko` 和 `NiKo` 是不同选手）。

输出包含：
- 游戏名、本名
- 国籍、战队、位置（IGL/Entry/Support/AWPer/Lurker）
- Rating 3.0、KPR、DPR、ADR、KAST、Round Swing、K/D
- 总比赛数、总奖金
- 不同级别对手的Rating（vs top5/10/20/30/50）
- HLTV Top20 入选记录

### `-e event -i <eventId>` — 赛事完整赛程

查看某赛事的全部比赛（按日期扫描）：

```bash
python fetcher.py -e event -i 8914 -p
```

输出包含该赛事从开始到结束日期的所有比赛，按时间排序。

### `-i` / `--id` — 比赛/赛事详情

查看单场比赛的详细信息。

```bash
python fetcher.py -e match -i 2395575 -p
```

详情包括：
- 赛事名称、奖金
- 对阵双方、排名、比分、赛制
- 地图 Ban/Pick
- 各图比分（半场详情）
- **选手数据**：Rating、ADR、KAST、K/D/A
- 直播信息（如有）

> 选手数据来自 `getMatchDetail` 的 `statsDTOList` 聚合条目，与HLTV完美匹配（仅对已结束比赛可用）。

### `--status` — 状态筛选

筛选比赛/赛事状态：

| 值 | 含义 |
|----|------|
| `1` | 进行中 |
| `2` | 即将开始 |
| `3` | 已结束 |

```bash
# 今天已结束的比赛
python fetcher.py -e match -p --status 3

# 查看某天已结束比赛
python fetcher.py -e match -p -d 2026-07-01 --status 3
```

### `-d` / `--date` — 指定日期

```bash
# 查看历史比赛
python fetcher.py -e match -p -d 2026-07-01

# 查看历史已结束比赛
python fetcher.py -e match -p -d 2026-07-01 --status 3
```

### `--history` — 历史赛事

从比赛记录中反推已结束的赛事（赛事API本身不返回已结束赛事）。

```bash
# 扫描近30天历史赛事
python fetcher.py -e event --history -p

# 指定天数
python fetcher.py -e event --history 7 -p

# 只显示已结束赛事
python fetcher.py -e event --history 30 -p --status 3
```

> 注意：会逐一查询每天的比赛数据，30天约需45秒（内置1.5秒请求间隔）。

### `--format` — 输出格式

```bash
# 原始JSON
python fetcher.py -e match --raw

# CSV文件
python fetcher.py -e team --format csv

# JSON文件
python fetcher.py -e match --raw -o matches.json
```

### `--limit` — 显示行数

```bash
python fetcher.py -e match -p --limit 5
```

### `--spotlight` — 当前聚焦赛事

自动找出进行中奖金最高的焦点赛事，支持4种查询：

```bash
# 查看当前聚焦赛事是什么
python fetcher.py --spotlight

# 重要赛事最近比赛
python fetcher.py -e match --spotlight --recent 10 -p

# 重要赛事正在进行的比赛
python fetcher.py -e match --spotlight --status 1 -p

# 重要赛事即将开始的比赛
python fetcher.py -e match --spotlight --status 2 -p
```

### `--rate` — 请求间隔

控制API请求频率，默认1.5秒：

```bash
python fetcher.py -e event --history 30 -p --rate 3
```

## API端点

工具自动管理HMAC-SHA256签名，无需手动处理认证。

| 端点 | 说明 |
|------|------|
| `getMatchList` | 比赛列表（按日期） |
| `getMatchDetail` | 比赛详情（含选手比赛数据聚合） |
| `team/rank` | 战队HLTV排名 |
| `team/detail` | 战队详情（含队员阵容） |
| `event/getEventList` | 赛事列表（仅进行中/即将） |
| `event/getEventDetail` | 赛事详情 |
| `match/fuzzySearch` | 全局模糊搜索（选手/战队/赛事，选手名称→ID解析） |
| `player/stat/detail` | 选手详情（本名、战队、Rating 3.0、Top20 等） |
| `player/mvp/top` | MVP排行 |
| `player/stats` | 选手排行榜 |
| `player/transfer` | 选手转会信息 |
| `match/getMatchPlayerStats` | 选手逐图统计数据（开发者用，非必须） |
| `getPlayerAnalysisByMatch` | 选手比赛分析（已弃用，数据有错位） |

## 完整示例

```bash
# 查看今天比赛
python fetcher.py -e match -p

# 最近10场已结束比赛
python fetcher.py -e match -p --recent 10

# FaZe最近5场比赛
python fetcher.py -e match -p --recent 5 --team FaZe

# 当前聚焦赛事
python fetcher.py --spotlight

# 重要赛事最近10场已结束比赛
python fetcher.py -e match --spotlight --recent 10 -p

# 重要赛事即将开始的比赛
python fetcher.py -e match --spotlight --status 2 -p

# 查看战队排名（含队员名单）
python fetcher.py -e team -p

# 查看比赛详情（含地图B/P、选手数据）
python fetcher.py -e match -i 2395575 -p

# 查看赛事完整赛程
python fetcher.py -e event -i 8914 -p

# 查看近期历史赛事
python fetcher.py -e event --history 7 -p

# 查看7天内已结束的赛事
python fetcher.py -e event --history 7 -p --status 3

# 查看某天已结束比赛
python fetcher.py -e match -p -d 2026-07-01 --status 3

# 保存为JSON
python fetcher.py -e match --raw -o matches.json

# 保存为CSV
python fetcher.py -e team --format csv -o teams.csv

# 查看选手详情（游戏名查，大小写敏感）
python fetcher.py -e player --player-name donk -p
python fetcher.py -e player --player-name niko -p   # 小写niko

# 查看选手详情（ID查）
python fetcher.py -e player -i 21167 -p

# 查看选手MVP排行
python fetcher.py -e player -p

# 查看选手转会
python fetcher.py -e transfer -p

# 自定义请求频率
python fetcher.py -e match -p --rate 3.0
```

## 项目结构

```
CSProLeagueAPI/
├── fetcher.py            # 数据获取主脚本
├── discover.py           # API探索工具（可选，需 Playwright）
├── apis.json             # API配置（自动生成，.gitignore）
├── apis.json.example     # API配置模板（含全部已知端点）
├── requirements.txt      # 依赖
├── .gitignore
└── README.md             # 本文件
```

> `apis.json` 由 `discover.py` 或首次运行 `fetcher.py`（从模板复制）自动生成。
> 如 API 失效，删除 `apis.json` 重启即可恢复默认配置，或运行 `discover.py` 重新发现。

## 注意事项

- 请求间隔默认1.5秒，避免触发风控
- 签名密钥从完美世界JS源码提取，内置在 `apis.json.example` 中
- `apis.json` 已加入 `.gitignore`，不包含在版本控制中
- 首次运行 `fetcher.py` 会自动从 `apis.json.example` 创建 `apis.json`
- `discover.py` 为可选工具，仅用于重新发现API端点（需 Playwright）