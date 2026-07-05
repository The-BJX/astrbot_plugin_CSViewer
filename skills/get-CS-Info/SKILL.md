# Skill: CS 职业联赛数据查询

使用 `fetcher.py` 从完美世界CS电竞数据中心查询CS职业比赛信息。

## 安装

```bash
pip install -r requirements.txt
```

## 快速参考

```bash
python fetcher.py -e <endpoint> <flags>
```

### 数据端点 (-e)

| 值 | 说明 |
|----|------|
| `match` | 比赛列表、详情、历史 |
| `team` | 战队排名 |
| `event` | 赛事列表、赛程 |
| `player` | 选手数据、MVP排行 |
| `transfer` | 选手转会 |

### 核心参数

| 参数 | 说明 |
|------|------|
| `-p` | 人类友好输出（隐藏ID/URL等技术字段） |
| `-i <ID>` | 查看详情（比赛用matchId，赛事用eventId） |
| `--recent <N>` | 最近N场已结束比赛 |
| `--status <1\|2\|3>` | 筛选状态：1=进行 2=即将 3=结束 |
| `-d <YYYY-MM-DD>` | 指定日期 |
| `--team <名字>` | 按战队名筛选（模糊匹配） |
| `--history [天数]` | 从比赛记录提取历史赛事 |
| `--spotlight` | 当前聚焦赛事（奖金最高的进行中赛事） |
| `--limit <N>` | 限制显示行数 |
| `--rate <秒>` | 请求间隔（默认1.5秒） |
| `--raw` | 输出原始JSON |
| `--format <json\|csv\|table>` | 输出格式 |
| `-o <文件>` | 保存到文件 |

## 常用查询

### 当前赛程
```bash
# 今天比赛
python fetcher.py -e match -p

# 某天比赛
python fetcher.py -e match -p -d 2026-07-01

# 今天已结束的比赛
python fetcher.py -e match -p --status 3
```

### 历史比赛
```bash
# 最近10场已结束比赛（逆向逐日扫描）
python fetcher.py -e match -p --recent 10

# 某天已结束比赛
python fetcher.py -e match -p -d 2026-07-01 --status 3
```

### 战队筛选
```bash
# 某战队最近5场比赛
python fetcher.py -e match -p --recent 5 --team FaZe

# 查看某战队今天赛程
python fetcher.py -e match -p --team Spirit
```

### 比赛详情（含选手数据）
```bash
python fetcher.py -e match -i <matchId> -p
```
返回：赛事信息、对阵比分、地图Ban/Pick、各图比分、**选手Rating/ADR/KPR/DPR/KAST**、直播信息。

### 聚焦赛事（自动选择奖金最高的进行中赛事）
```bash
# 查看当前聚焦赛事
python fetcher.py --spotlight

# 聚焦赛事最近比赛
python fetcher.py -e match --spotlight --recent 10 -p

# 聚焦赛事即将开始的比赛
python fetcher.py -e match --spotlight --status 2 -p
```

### 赛事查询
```bash
# 赛事列表（当前）
python fetcher.py -e event -p

# 赛事完整赛程（自动扫描日期范围）
python fetcher.py -e event -i <eventId> -p

# 历史赛事（从近30天比赛记录提取）
python fetcher.py -e event --history 30 -p --status 3
```

### 排名与选手
```bash
# 战队排名（含队员名单）
python fetcher.py -e team -p

# 选手MVP排行
python fetcher.py -e player -p

# 选手转会信息
python fetcher.py -e transfer -p
```

### 数据导出
```bash
python fetcher.py -e match --raw -o matches.json
python fetcher.py -e team --format csv -o teams.csv
```

## 输出格式说明

比赛列表 `-e match -p` 输出列：
```
ID   赛事    奖金    战队（含排名）  比分  状态  赛制  时间
```

比赛详情 `-i <matchId> -p` 输出：
- 赛事名称、奖金
- 对阵双方（含排名）、比分、赛制、时间、状态
- 地图 Ban/Pick（Ban / Pick / Decider）
- 各图比分（半场1 / 半场2 / 总计）
- 选手数据（Rating、ADR、KPR、DPR、KAST）

战队排名 `-e team -p` 输出列：
```
排名  战队  积分  地区  变动  队员
```

## 状态值说明

| 字段值 | 显示 | 含义 |
|--------|------|------|
| `status=1` | 进行 | 比赛/赛事正在进行 |
| `status=2` | 即将 | 尚未开始 |
| `status=3` | 结束 | 已结束 |

## 注意

- 请求间隔默认1.5秒，防止触达频率限制，可使用 `--rate` 调整
- 所有API请求自动HMAC-SHA256签名，无需手动处理认证
- 历史扫描（`--recent` / `--history` / 赛程）会多次请求API，耗时与天数/场次成正比
- 选手数据仅对已结束比赛可用
