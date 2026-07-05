import argparse
import csv
import hashlib
import hmac
import json
import os
import re

import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich import box



MAP_NAMES = {
    1: "Cache", 2: "Dust2", 3: "Mirage", 4: "Inferno",
    5: "Nuke", 7: "Overpass",
    14: "Ancient", 15: "Anubis",
}

MAP_TYPE_LABEL = {1: "[red]Ban[/]", 2: "[green]Pick[/]", 3: "[yellow]Decider[/]"}

APIS_FILE = Path(__file__).parent / "apis.json"
CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_TTL_SEC = 300

DEFAULT_RATE_LIMIT = 1.5
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3

console = Console(stderr=False, force_terminal=False)


def load_apis():
    EXAMPLE_FILE = Path(__file__).parent / "apis.json.example"
    if not APIS_FILE.exists():
        if EXAMPLE_FILE.exists():
            import shutil
            shutil.copy(EXAMPLE_FILE, APIS_FILE)
            console.print(f"[green][✓] 已从 apis.json.example 创建 {APIS_FILE}[/green]")
        else:
            console.print(f"[red][!] 未找到 {APIS_FILE} 或 apis.json.example[/red]")
            console.print("[yellow]请运行 discover.py 或创建 apis.json 配置[/yellow]")
            sys.exit(1)
    with open(APIS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def url_encode(s):
    """Lo() - URL编码, %20->+, 处理特殊字符"""
    return urllib.parse.quote(s, safe="").replace("%20", "+").replace("%7E", "~")


def build_sign_string(params: dict | None, timestamp: str, nonce: str) -> str:
    """k1() - 构建待签名字符串"""
    n = {}
    for k, v in (params or {}).items():
        if isinstance(v, list):
            n[k] = [str(x) for x in v]
        else:
            n[k] = [str(v)]
    n["timestamp"] = [timestamp]
    n["nonce"] = [nonce]

    parts = []
    for key in sorted(n.keys()):
        for val in sorted(n[key]):
            parts.append(f"{url_encode(key)}={url_encode(val)}")
    return "&".join(parts)


def sign(params: dict | None, secret: str) -> tuple[str, str, str]:
    """生成 X-Timestamp, X-Nonce, X-Signature"""
    timestamp = str(int(time.time() * 1000))
    nonce = os.urandom(16).hex()
    string_to_sign = build_sign_string(params, timestamp, nonce)
    signature = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return timestamp, nonce, signature


class RateLimiter:
    def __init__(self, min_interval: float = DEFAULT_RATE_LIMIT):
        self.min_interval = min_interval
        self._last_call = 0.0

    def wait(self):
        now = time.monotonic()
        elapsed = now - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()


def sanitize_filename(s: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in s)


class APIClient:
    def __init__(self, apis: dict, rate_limit: float = DEFAULT_RATE_LIMIT):
        self.apis = apis
        self.api_base = apis.get("api_base", "https://esports.wanmei.com")
        self.secret = apis.get("sign_secret", "")
        self.cookies = apis.get("cookies", {})

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://data.wanmei.com/csgo",
            "Origin": "https://data.wanmei.com",
        })
        if self.cookies:
            for name, value in self.cookies.items():
                self.session.cookies.set(name, value, domain="esports.wanmei.com")

        self.limiter = RateLimiter(rate_limit)

    def _sign_headers(self, params: dict | None) -> dict:
        ts, nonce, sig = sign(params, self.secret)
        return {
            "X-Timestamp": ts,
            "X-Nonce": nonce,
            "X-Signature": sig,
        }

    def request(
        self, method: str, path: str, params: dict | None = None
    ) -> dict | None:
        url = f"{self.api_base}{path}"
        self.limiter.wait()

        headers = self._sign_headers(params)
        all_headers = {**self.session.headers, **headers}

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    headers=all_headers,
                    timeout=DEFAULT_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()

                if isinstance(data, dict) and data.get("code") == 1004:
                    console.print(f"[yellow]  认证失败 ({data.get('msg','')}), 重试 ({attempt}/{MAX_RETRIES})[/yellow]")
                    time.sleep(attempt)
                    headers = self._sign_headers(params)
                    all_headers = {**self.session.headers, **headers}
                    continue

                return data

            except requests.exceptions.HTTPError as e:
                if attempt < MAX_RETRIES:
                    wait = attempt * 2
                    console.print(f"[yellow]  HTTP {e.response.status_code}, {wait}s后重试 ({attempt}/{MAX_RETRIES})[/yellow]")
                    time.sleep(wait)
                else:
                    console.print(f"[red]  HTTP错误: {e.response.status_code} {path}[/red]")
            except requests.exceptions.Timeout:
                if attempt < MAX_RETRIES:
                    console.print(f"[yellow]  超时, 重试 ({attempt}/{MAX_RETRIES})[/yellow]")
                else:
                    console.print(f"[red]  请求超时: {path}[/red]")
            except requests.exceptions.RequestException as e:
                console.print(f"[red]  请求失败: {e}[/red]")
                return None
        return None


PRETTY_FIELDS = {
    "match": {
        "cols": ["ID", "赛事", "奖金", "战队", "比分", "状态", "赛制", "时间"],
        "hide_keys": {"id", "matchId", "eventId", "team1Id", "team2Id", "stageId",
                       "namiMatchId", "namiTeam1Id", "namiTeam2Id", "newsId", "roomId",
                       "logoBlack", "logoWhite", "logo", "teamLogo", "teamLogoBlack",
                       "background", "thumbnail", "img", "playerDTOList",
                       "liveRoomInfo", "liveRoomInfoList", "mapBPDTOS",
                       "matchDetailDTO", "matchIdList", "singleMatchDataDTOS",
                       "statsDTOList", "performanceStatsList", "dataMapStatistics",
                       "subscribeStatus", "hasPredict", "venue", "platform",
                       "prizeList", "regionDTO", "teamDTOList", "talkId",
                       "completedStatus", "description", "topic", "weight", "type"},
    },
    "team": {
        "cols": ["排名", "战队", "积分", "地区", "变动", "队员"],
        "hide_keys": {"hltvId", "teamId", "teamLogo", "teamLogoBlack", "logo",
                       "logoBlack", "logoWhite", "vrsRank", "id", "isSupportTeam",
                       "location", "talkId"},
    },
    "event": {
        "cols": ["赛事名称", "中文名", "奖金", "开始", "结束", "状态"],
        "hide_keys": {"id", "eventId", "background", "logo", "thumbnail",
                       "teamDTOList", "regionDTO", "prizeList", "publishTime",
                       "scheduledTime", "publishType", "topic", "weight", "type",
                       "level", "liveType", "hot", "important", "subImportant",
                       "teamNumber", "description"},
    },
    "player": {
        "cols": ["选手", "队伍", "Rating", "ADR", "KAST", "KPR", "存活率"],
        "hide_keys": {"playerId", "teamId", "img", "teamImage", "logo", "logoBlack",
                       "logoWhite", "allEvents", "onlyBigEvents", "count", "eventId",
                       "eventName"},
    },
    "transfer": {
        "cols": ["选手", "原战队", "新战队", "时间"],
        "hide_keys": {"playerId", "fromTeamId", "toTeamId", "fromTeamLogo",
                       "toTeamLogo", "logo", "coach", "id", "toStatus"},
    },
    "default": {
        "cols": [],
        "hide_keys": set(),
    },
}


def _ts_to_str(ts: int | str | None) -> str:
    if not ts:
        return ""
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%m-%d %H:%M")
        except Exception:
            return ts[:16].replace("T", " ")
    try:
        return datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M")
    except Exception:
        return str(ts)


def _status_str(s: int | None) -> str:
    return {1: "[green]进行[/]", 2: "[cyan]即将[/]", 3: "[yellow]结束[/]",
            4: "[red]取消[/]"}.get(s, f"未知({s})") if s else ""


def _safe_str(val) -> str:
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        return ""
    s = str(val)
    if s.startswith("http"):
        return ""
    return s


def _pick(obj: dict, *keys):
    for k in keys:
        v = obj.get(k)
        if v is not None and v != "":
            return v
    return None


def display_pretty(items: list, endpoint_key: str, data: dict, args: argparse.Namespace):
    k = endpoint_key.lower()
    ep_type = "match" if "/getmatchlist" in k else \
              "team" if "/team/rank" in k else \
              "event" if "/event/" in k else \
              "transfer" if "/transfer" in k else \
              "player" if "/player/" in k else "default"
    cfg = PRETTY_FIELDS.get(ep_type, PRETTY_FIELDS["default"])
    hide = cfg["hide_keys"]

    if ep_type == "match":
        rows = []
        for m in items:
            t1 = m.get("team1DTO") or {}
            t2 = m.get("team2DTO") or {}
            evt = m.get("csgoEventDTO") or {}
            s1, s2 = m.get("score1"), m.get("score2")
            winner_id = m.get("winnerTeamId")
            t1_id = t1.get("teamId")
            t2_id = t2.get("teamId")
            s1_s = str(s1) if s1 is not None else "-"
            s2_s = str(s2) if s2 is not None else "-"
            t1_name = _safe_str(_pick(t1, 'name'))
            t2_name = _safe_str(_pick(t2, 'name'))
            r1 = t1.get("rank", "")
            r2 = t2.get("rank", "")
            if winner_id and str(winner_id) == str(t1_id):
                t1_name = f"[bold]{t1_name}[/]"
            elif winner_id and str(winner_id) == str(t2_id):
                t2_name = f"[bold]{t2_name}[/]"
            t1_disp = f"{t1_name}" + (f" (#{r1})" if r1 else "")
            t2_disp = f"{t2_name}" + (f" (#{r2})" if r2 else "")
            teams = f"{t1_disp} vs {t2_disp}"
            score = f"{s1_s}:{s2_s}"
            rows.append([
                str(m.get("matchId", "")),
                _safe_str(_pick(evt, "nameZh", "name")),
                _safe_str(_pick(evt, "prize")),
                teams,
                score,
                _status_str(m.get("status")),
                _safe_str(m.get("bo", "")),
                _ts_to_str(m.get("startTime")),
            ])
        table = Table(show_header=True, header_style="bold", box=box.ROUNDED)
        for col in cfg["cols"]:
            table.add_column(col)
        for row in rows:
            table.add_row(*row)
        console.print(table)

    elif ep_type == "team":
        table = Table(show_header=True, header_style="bold", box=box.ROUNDED)
        for col in cfg["cols"]:
            table.add_column(col)
        for t in items:
            players = t.get("players") or []
            names = ", ".join(p.get("name", "") for p in players[:5] if p.get("name")) if isinstance(players, list) else ""
            table.add_row(
                str(t.get("rank", "")),
                _safe_str(t.get("teamName", "")),
                str(t.get("score", "")),
                {1: "欧洲", 2: "美洲", 3: "亚洲", 4: "大洋洲", 5: "非洲",
                 6: "中国", 7: "其他"}.get(t.get("region"), _safe_str(t.get("region"))),
                str(t.get("rankingChanges", "")),
                names,
            )
        console.print(table)

    elif ep_type == "event":
        table = Table(show_header=True, header_style="bold", box=box.ROUNDED)
        for col in cfg["cols"]:
            table.add_column(col)
        for e in items:
            table.add_row(
                _safe_str(_pick(e, "name")),
                _safe_str(_pick(e, "nameZh")),
                _safe_str(_pick(e, "prize")),
                _ts_to_str(e.get("startTime")),
                _ts_to_str(e.get("endTime")),
                _status_str(e.get("status")),
            )
        console.print(table)

    elif ep_type == "player":
        if items and "count" in items[0]:
            cols = ["选手", "MVP次数", "大赛MVP"]
            table = Table(show_header=True, header_style="bold", box=box.ROUNDED)
            for col in cols:
                table.add_column(col)
            for p in items:
                big = p.get("onlyBigEvents", {}).get("Total", "-")
                table.add_row(
                    _safe_str(p.get("playerName", "")),
                    str(p.get("count", "")),
                    str(big),
                )
        else:
            cols = ["选手", "队伍", "Rating", "ADR", "KAST", "KPR", "存活率"]
            table = Table(show_header=True, header_style="bold", box=box.ROUNDED)
            for col in cols:
                table.add_column(col)
            for p in items:
                table.add_row(
                    _safe_str(_pick(p, "playerName", "name")),
                    _safe_str(_pick(p, "teamName")),
                    _safe_str(p.get("rating", "")),
                    _safe_str(p.get("adr", "")),
                    _safe_str(p.get("kast", "")),
                    _safe_str(p.get("kpr", "")),
                    _safe_str(p.get("survive", "")),
                )
        console.print(table)

    elif ep_type == "transfer":
        table = Table(show_header=True, header_style="bold", box=box.ROUNDED)
        for col in cfg["cols"]:
            table.add_column(col)
        for t in items:
            table.add_row(
                _safe_str(_pick(t, "name")),
                _safe_str(t.get("fromTeamName", "")),
                _safe_str(t.get("toTeamName", "")),
                _ts_to_str(t.get("transferTime")),
            )
        console.print(table)

    else:
        table = Table(show_header=True, header_style="bold", box=box.ROUNDED)
        if items and isinstance(items[0], dict):
            visible = [k for k in items[0] if k not in hide and not k.lower().endswith(("id", "url", "logo", "img"))][:args.max_cols]
            for col in visible:
                table.add_column(col)
            for item in items:
                row = []
                for col in visible:
                    val = item.get(col, "")
                    if isinstance(val, (dict, list)):
                        val = json.dumps(val, ensure_ascii=False)[:60]
                    row.append(str(val)[:40])
                table.add_row(*row)
        if table.columns:
            console.print(table)

    console.print(f"[dim]共 {len(items)} 条[/dim]")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        console.print(f"[green]已保存至 {args.output}[/green]")


def display_match_detail(m: dict, args: argparse.Namespace, client=None):
    from rich.columns import Columns

    evt = m.get("csgoEventDTO") or {}
    t1 = m.get("team1DTO") or {}
    t2 = m.get("team2DTO") or {}
    s1, s2 = m.get("score1"), m.get("score2")
    status = m.get("status")
    bo = m.get("bo", "")
    start = _ts_to_str(m.get("startTime"))

    console.print()
    console.print(Panel.fit(
        f"[bold]{_safe_str(_pick(evt, 'nameZh', 'name'))}[/bold]\n"
        f"[dim]{_safe_str(_pick(evt, 'prize'))}[/dim]"
    ))

    score_str = f"{s1}:{s2}" if s1 is not None else "?:?"
    t1_rank = f" (#{t1.get('rank')})" if t1.get('rank') else ""
    t2_rank = f" (#{t2.get('rank')})" if t2.get('rank') else ""

    info = Table.grid(padding=(1, 4))
    info.add_row(
        f"[bold cyan]{_safe_str(t1.get('name'))}[/]{t1_rank}",
        f"[bold]{score_str}[/]",
        f"[bold cyan]{_safe_str(t2.get('name'))}[/]{t2_rank}"
    )
    info.add_row(
        f"[dim]地区: {_safe_str(t1.get('location'))}[/]" if t1.get('location') else "",
        _status_str(status),
        f"[dim]地区: {_safe_str(t2.get('location'))}[/]" if t2.get('location') else "",
    )
    console.print(info)
    console.print(f"[dim]BO: {bo}  |  开始: {start}[/dim]")

    # Map B/P
    bp_list = m.get("mapBPDTOS")
    if bp_list:
        console.print("\n[bold]地图Ban/Pick:[/bold]")
        bp_table = Table(show_header=True, box=box.SIMPLE)
        bp_table.add_column("地图")
        bp_table.add_column("操作")
        bp_table.add_column("队伍")
        for bp in bp_list:
            map_name = _safe_str(bp.get("mapName", MAP_NAMES.get(bp.get("mapId"), f"图{bp.get('mapId')}")))
            op = MAP_TYPE_LABEL.get(bp.get("type"), f"未知({bp.get('type')})")
            team = _safe_str(bp.get("teamName", ""))
            bp_table.add_row(map_name, op, team)
        console.print(bp_table)

    # Per-map scores
    maps = m.get("singleMatchDataDTOS", [])
    if maps:
        console.print("\n[bold]各图比分:[/bold]")
        map_table = Table(show_header=True, box=box.SIMPLE)
        map_table.add_column("地图")
        map_table.add_column("半场1")
        map_table.add_column("半场2")
        map_table.add_column("总计")
        for sm in maps:
            mid = sm.get("mapId")
            map_name = MAP_NAMES.get(mid, f"图{mid}")
            home = _safe_str(sm.get("homeTeamName", ""))
            away = _safe_str(sm.get("awayTeamName", ""))
            sc = sm.get("scores")
            if isinstance(sc, dict):
                s1_v = sc.get("homeScore", "?")
                s2_v = sc.get("awayScore", "?")
                map_table.add_row(f"{map_name}\n({home} vs {away})", str(s1_v), str(s2_v), f"{s1_v}:{s2_v}")
            else:
                halves = str(sc).split(",")
                parts = [h.split(":") for h in halves if ":" in h]
                if len(parts) >= 1:
                    # API format: "team1_T:team1_CT[:ot...],team2_CT:team2_T[:ot...]"
                    # Display as halves from home team's perspective:
                    #   half1 = team1_T : team2_CT
                    #   half2 = team1_CT : team2_T
                    t1_t = int(parts[0][0])   # team1 T-side
                    t1_ct = int(parts[0][1])  # team1 CT-side
                    t2_ct = int(parts[1][0])  # team2 CT-side
                    t2_t = int(parts[1][1])   # team2 T-side
                    t1_ot = [int(x) for x in parts[0][2:]]
                    t2_ot = [int(x) for x in parts[1][2:]]
                    ot1 = sum(t1_ot)
                    ot2 = sum(t2_ot)
                    half1 = f"{t1_t}:{t2_ct}" + (f" (加时+{ot1})" if ot1 else "")
                    half2 = f"{t1_ct}:{t2_t}" + (f" (加时+{ot2})" if ot2 else "")
                    t1_total = t1_t + t1_ct + ot1
                    t2_total = t2_ct + t2_t + ot2
                    map_table.add_row(f"{map_name}\n({home} vs {away})",
                                      half1, half2, f"{t1_total}:{t2_total}")
                else:
                    map_table.add_row(map_name, "-", "-")
        console.print(map_table)

    # Player match stats (from statsDTOList match-level aggregate)
    stats_list = m.get("statsDTOList")
    if client and m.get("status") == 3 and stats_list:
        console.print("\n[bold]选手数据:[/bold]")

        match_agg = None
        for entry in stats_list:
            rounds = (entry.get("score1", 0) or 0) + (entry.get("score2", 0) or 0)
            if rounds > 0 and rounds <= 5:
                match_agg = entry
                break

        if match_agg:
            for plist, tm_name in [
                (match_agg.get("team1PlayerStatsDTOList", []), _safe_str(t1.get("name"))),
                (match_agg.get("team2PlayerStatsDTOList", []), _safe_str(t2.get("name"))),
            ]:
                if not plist:
                    continue
                stat_table = Table(show_header=True, box=box.SIMPLE, title=tm_name)
                stat_table.add_column("选手")
                stat_table.add_column("Rating")
                stat_table.add_column("击杀")
                stat_table.add_column("死亡")
                stat_table.add_column("助攻")
                stat_table.add_column("ADR")
                stat_table.add_column("KAST")
                for p in sorted(plist, key=lambda x: x.get("kills", 0) or 0, reverse=True):
                    pd = p.get("playerDTO", {}) or {}
                    pname = pd.get("name", "") or p.get("name", "?")
                    stat_table.add_row(
                        _safe_str(pname),
                        f"{p.get('rating', ''):.2f}" if p.get('rating') else "",
                        str(p.get("kills", "")),
                        str(p.get("deaths", "")),
                        str(p.get("assists", "")),
                        f"{p.get('adr', ''):.1f}" if p.get('adr') else "",
                        f"{p.get('kast', ''):.1f}%" if p.get('kast') or p.get('kast') == 0 else "",
                    )
                console.print(stat_table)
        else:
            console.print("[dim]选手数据暂不可用[/dim]")
    elif client and m.get("status") == 3:
        console.print("[dim]选手数据暂不可用[/dim]")

    # Player rosters (fallback for live matches)
    ps_list = m.get("performanceStatsList")
    if ps_list and m.get("status") != 3:
        console.print("\n[bold]选手阵容:[/bold]")
        for entry in ps_list:
            for side_list in [entry.get("team1List", []), entry.get("team2List", [])]:
                names = "  ".join(f"[cyan]{p.get('name', '?')}[/]" for p in side_list)
                if names:
                    console.print(names)
            break

    # Live room
    rooms = m.get("liveRoomInfoList", [])
    if rooms:
        room_strs = [f"{r.get('platform')}/{r.get('roomId')}" for r in rooms]
        console.print(f"\n[bold]直播:[/bold] {', '.join(room_strs)}")


def fetch_event_history(client: APIClient, days_back: int = 30, min_matches: int = 1) -> list:
    """从历史比赛数据中提取已结束的赛事, 自动限速"""
    from datetime import timedelta, date

    seen = {}
    today = date.today()
    total_requests = days_back
    success_count = 0

    console.print(f"[dim]每日1次请求, 间隔{client.limiter.min_interval}秒, 约需{total_requests * client.limiter.min_interval:.0f}秒[/dim]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"扫描 {days_back} 天比赛记录...", total=total_requests)

        for i in range(days_back):
            d = today - timedelta(days=i)
            date_str = d.strftime("%Y-%m-%d")
            progress.update(task, description=f"查询 {date_str}", advance=0)

            data = client.request(
                "GET",
                "/apg/eventcenter/csgo/getMatchList",
                {"matchTime": f"{date_str} 00:00:00"},
            )
            progress.update(task, advance=1)

            if not data:
                continue
            success_count += 1
            matches = extract_items(data)
            for m in matches:
                evt = m.get("csgoEventDTO")
                if not evt:
                    continue
                eid = evt.get("eventId")
                if eid and eid not in seen:
                    seen[eid] = {**evt, "_match_count": 0, "_last_match": date_str}
                if eid:
                    seen[eid]["_match_count"] += 1
                    seen[eid]["_last_match"] = date_str

    result = [v for v in seen.values() if v["_match_count"] >= min_matches]
    result.sort(key=lambda x: x.get("endTime") or 0, reverse=True)
    console.print(f"[dim]成功{success_count}/{total_requests}天, 发现{len(result)}个赛事[/dim]")
    return result


def resolve_team(client: APIClient, name: str) -> dict | None:
    """按名字模糊查找战队, 支持缩写/别名"""
    data = client.request("GET", "/apg/eventcenter/csgo/team/rank", {"page": "1", "pageSize": "200", "type": "0"})
    if not data:
        return None
    teams = extract_items(data)
    name_lower = name.lower().strip()

    # Build alias map from ranked team names
    # e.g. "navi" → "Natus Vincere", "lvg" → "Lynn Vision"
    alias_map = {}
    for t in teams:
        tn = t.get("teamName", "")
        tn_lower = tn.lower()
        words = re.split(r"[\s.]+", tn_lower)
        words = [w for w in words if w]
        # First-letter abbreviation: "Natus Vincere" → "nv", "BC.G" → "bcg"
        if len(words) > 1:
            abbr = "".join(w[0] for w in words if w)
            if abbr:
                alias_map[abbr] = tn
        # Also strip dots for matching: "bc.g" → "bcg"
        clean = tn_lower.replace(".", "")
        if clean != tn_lower:
            alias_map[clean] = tn
        # Known common aliases
        if tn_lower == "natus vincere":
            for a in ["navi", "na-vi", "navinatusvincere"]:
                alias_map[a] = tn
        elif tn_lower == "lynn vision":
            alias_map["lvg"] = tn
        elif tn_lower == "virtus.pro":
            alias_map["vp"] = tn
        elif tn_lower == "mouz":
            alias_map["mousesports"] = tn
        elif "." in tn and len(words) > 1:
            abbr = "".join(w[0] for w in words if w)
            alias_map[abbr] = tn
            alias_map[tn_lower.replace(".", "")] = tn
        elif tn_lower == "natus vincere junior":
            alias_map["navi jr"] = tn

    # Check alias map first
    if name_lower in alias_map:
        target = alias_map[name_lower]
        for t in teams:
            if (t.get("teamName") or "").lower() == target.lower():
                hltv_id = t.get("hltvId") or t.get("teamId")
                return {"teamId": hltv_id, "name": t.get("teamName"), "hltvId": t.get("hltvId")}

    # Fuzzy match: substring, or match any word, or match by abbreviation
    candidates = []
    for t in teams:
        tn = (t.get("teamName") or "").lower()
        # Direct substring
        if name_lower in tn:
            candidates.append((0, t))
            continue
        # Any word in team name starts with query
        words = tn.split()
        for w in words:
            if w.startswith(name_lower) or name_lower.startswith(w):
                candidates.append((1, t))
                break
        # Query matches abbreviation of multi-word team
        if len(words) > 1:
            abbr = "".join(w[0] for w in words if w)
            if abbr.startswith(name_lower) or name_lower.startswith(abbr):
                candidates.append((2, t))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        t = candidates[0][1]
        hltv_id = t.get("hltvId") or t.get("teamId")
        return {"teamId": hltv_id, "name": t.get("teamName"), "hltvId": t.get("hltvId")}
    return None


def display_team_detail(client: APIClient, detail: dict, args: argparse.Namespace):
    name = detail.get("name", "")
    hltv_rank = detail.get("hltvRank")
    hltv_score = detail.get("hltvScore")
    hltv_id = detail.get("hltvId")
    vrs_rank = detail.get("rank")
    vrs_score = detail.get("score")

    console.print()
    console.print(Panel.fit(f"[bold yellow]{name}[/bold yellow]"))

    # Rankings
    rank_parts = []
    if hltv_rank is not None:
        rank_parts.append(f"HLTV: #{hltv_rank} ({hltv_score}pts)")
    if vrs_rank is not None:
        rank_parts.append(f"VRS: #{vrs_rank} ({vrs_score}pts)")
    if rank_parts:
        console.print("  ".join(rank_parts))
    console.print(f"  ID: {hltv_id}")

    # Roster
    players = detail.get("playerList", []) or []
    if players:
        console.print("\n[bold]阵容:[/bold]")
        pos_names = {1: "IGL", 2: "Entry", 3: "Support", 4: "AWPer", 5: "Lurker"}
        ptable = Table(show_header=True, box=box.SIMPLE)
        ptable.add_column("选手")
        ptable.add_column("角色")
        ptable.add_column("Rating")
        ptable.add_column("年龄")
        ptable.add_column("国家")
        ptable.add_column("入队")
        ptable.add_column("比赛")
        for p in players:
            is_coach = p.get("coach", False)
            pname = p.get("name", "")
            if is_coach:
                pname += " [dim](教练)[/dim]"
            role = pos_names.get(p.get("position"), "")
            role_str = _safe_str(role) if role else ""
            rating = p.get("rating")
            age = p.get("age")
            country = p.get("country", "")
            time_on = p.get("timeOnTeam", "")
            maps = p.get("maps", "")
            ptable.add_row(
                pname,
                role_str,
                f"{rating:.2f}" if rating else "",
                str(age) if age else "",
                _safe_str(country),
                _safe_str(time_on),
                str(maps) if maps else "",
            )
        console.print(ptable)

    # Recent transfers for this team
    transfers = client.request("GET", "/apg/eventcenter/csgo/player/transfer",
                               {"pageNum": "1", "pageSize": "10"})
    if transfers and transfers.get("code") == 0:
        team_transfers = []
        for t in (transfers.get("result") or []):
            if (t.get("toTeamName") and name.lower() in t["toTeamName"].lower()) or \
               (t.get("fromTeamName") and name.lower() in t["fromTeamName"].lower()):
                team_transfers.append(t)
        if team_transfers:
            console.print("\n[bold]最近转会:[/bold]")
            ttable = Table(show_header=True, box=box.SIMPLE)
            ttable.add_column("选手")
            ttable.add_column("原战队")
            ttable.add_column("新战队")
            ttable.add_column("时间")
            for t in team_transfers:
                ttable.add_row(
                    _safe_str(t.get("name", "")),
                    _safe_str(t.get("fromTeamName", "")),
                    _safe_str(t.get("toTeamName", "")),
                    _ts_to_str(t.get("transferTime")),
                )
            console.print(ttable)


def resolve_player(client: APIClient, name: str) -> dict | None:
    """按名字模糊查找选手, 使用match/fuzzySearch"""
    data = client.request("GET", "/apg/eventcenter/csgo/match/fuzzySearch", {"name": name})
    if not data or data.get("code") != 0:
        return None
    result = data.get("result", {})
    players = result.get("players", [])
    if not players:
        return None
    # Priority: exact case-sensitive match > exact case-insensitive > first result
    name_raw = name.strip()
    name_lower = name_raw.lower()
    for p in players:
        pn = p.get("name", "")
        if pn == name_raw:
            return {"playerId": p.get("hltvId"), "playerName": pn}
    for p in players:
        pn = p.get("name", "")
        if pn.lower() == name_lower:
            return {"playerId": p.get("hltvId"), "playerName": pn}
    p = players[0]
    return {"playerId": p.get("hltvId"), "playerName": p.get("name")}


def display_player_profile(client: APIClient, player_id: int | str):
    data = client.request("GET", "/apg/eventcenter/csgo/player/stat/detail", {"playerId": str(player_id)})
    if not data or data.get("code") != 0:
        console.print(f"[red]查询选手 ID={player_id} 失败[/red]")
        return

    result = data.get("result", {})
    bi = result.get("basicInfo", {})
    avg = result.get("player_avg_data", {})
    stats = result.get("player_stats_data", {})
    featured = result.get("featured_ratings_data", {})

    if not bi:
        console.print(f"[yellow]未找到选手 ID={player_id}[/yellow]")
        return

    # Resolve position from team roster
    position_str = ""
    team_id = bi.get("teamId")
    if team_id:
        td = client.request("GET", "/apg/eventcenter/csgo/team/detail", {"teamId": str(team_id)})
        if td and td.get("code") == 0:
            team_res = td.get("result", {})
            player_list = team_res.get("playerList", [])
            pname = bi.get("playerName", "")
            pid = bi.get("playerId")
            pos_names = {1: "IGL", 2: "Entry", 3: "Support", 4: "AWPer", 5: "Lurker"}
            for pl in player_list:
                if pl.get("name", "").lower() == pname.lower() or pl.get("playerId") == pid:
                    pos = pl.get("position")
                    if pos in pos_names:
                        position_str = pos_names[pos]
                    break

    # Player name header
    console.print()
    header = f"[bold yellow]{bi.get('playerName', '?')}[/bold yellow]"
    rn = bi.get("realName")
    if rn:
        header += f" [dim]({rn})[/dim]"
    console.print(header)

    # Basic info line
    info_parts = []
    if bi.get("country"):
        info_parts.append(f"国籍: {bi['country']}")
    if bi.get("teamName"):
        info_parts.append(f"战队: {bi['teamName']}")
    if position_str:
        info_parts.append(f"位置: {position_str}")
    if bi.get("prize"):
        info_parts.append(f"奖金: {bi['prize']}")
    if stats.get("maps_played"):
        info_parts.append(f"比赛: {stats['maps_played']}场")
    if info_parts:
        console.print(Panel.fit("  |  ".join(info_parts)))

    # Rating row
    rating_val = avg.get("Rating", {}).get("score", "")
    kpr_val = avg.get("KPR", {}).get("score", "")
    dpr_val = avg.get("DPR", {}).get("score", "")
    adr_val = avg.get("ADR", {}).get("score", "")
    kast_val = avg.get("KAST", {}).get("score", "")
    swing_val = avg.get("Round_Swing", {}).get("score", "")

    stat_line = []
    if rating_val:
        stat_line.append(f"Rating 3.0: [bold]{rating_val}[/bold]")
    if kpr_val:
        stat_line.append(f"KPR: {kpr_val}")
    if dpr_val:
        stat_line.append(f"DPR: {dpr_val}")
    if adr_val:
        stat_line.append(f"ADR: {adr_val}")
    if kast_val:
        stat_line.append(f"KAST: {kast_val}")
    if swing_val:
        stat_line.append(f"Round Swing: {swing_val}")
    if stats.get("kills") and stats.get("deaths"):
        kd = int(stats['kills']) / max(int(stats['deaths']), 1)
        stat_line.append(f"K/D: {kd:.2f}")

    if stat_line:
        console.print(" | ".join(stat_line))

    # Featured ratings (vs top5, top10, etc.)
    f_ratings = featured.get("ratings", [])
    if f_ratings:
        rating_type = featured.get("rating_type", "Rating")
        parts = []
        for fr in f_ratings:
            parts.append(f"{fr['description']} {fr['value']} {fr['maps']}")
        console.print(f"[dim]{rating_type}[/dim]  " + "  |  ".join(parts))

    # Top20
    top20 = bi.get("top20", [])
    if top20:
        entries = []
        for entry in top20:
            entry_str = str(entry)
            match = re.match(r'#(\d+)\s+(\d+)', entry_str)
            if match:
                rank, year = match.groups()
                year_full = "20" + year if len(year) == 2 else year
                entries.append(f"{year_full} #{rank}")
            else:
                entries.append(entry_str)
        console.print(f"\n[bold]HLTV Top20:[/bold]  " + "  ".join(entries))

    # Honor list: API data unreliable, disabled for now


def parse_prize(s: str) -> int:
    """解析奖金字符串为数字，如 $1,000,000 → 1000000"""
    if not s:
        return 0
    s = str(s).replace(",", "").replace("$", "").strip()
    try:
        return int(float(s))
    except ValueError:
        return 0


def find_spotlight_event(client: APIClient) -> dict | None:
    """找出当前进行中奖金最高的赛事"""
    data = client.request("GET", "/apg/eventcenter/csgo/event/getEventList",
                          {"pageNum": "1", "pageSize": "50", "darkType": "1", "eventSubType": "5"})
    if not data:
        return None
    events = extract_items(data)
    ongoing = [e for e in events if e.get("status") == 1]
    if not ongoing:
        ongoing = [e for e in events if e.get("status") != 3]
    if not ongoing:
        return None
    ongoing.sort(key=lambda e: parse_prize(e.get("prize")), reverse=True)
    return ongoing[0]


def fetch_recent_matches(client: APIClient, count: int = 10, team_id: str = None) -> list:
    """扫描最近N场已结束比赛"""
    from datetime import timedelta, date
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

    if team_id:
        data = client.request("GET", "/apg/eventcenter/csgo/team/recent/matches",
                              {"teamId": team_id, "pageSize": str(count + 10)})
        if data and data.get("code") == 0:
            ml = data.get("result", {}).get("matchDTOList", []) or []
            if ml:
                # Convert to standard format
                converted = []
                team_name = data.get("result", {}).get("name", "")
                for m in ml:
                    s_team = m.get("homeScore")    # queried team's score
                    s_opp = m.get("awayScore")     # opponent's score
                    # team1DTO = queried team, score1 = that team's score
                    converted.append({
                        "matchId": m.get("matchId"),
                        "status": 3,
                        "startTime": m.get("startTime"),
                        "score1": s_team,
                        "score2": s_opp,
                        "bo": "",
                        "csgoEventDTO": {"name": m.get("eventName", ""), "nameZh": m.get("eventName", "")},
                        "team1DTO": {"name": team_name, "teamId": team_id},
                        "team2DTO": {"name": m.get("teamName", ""), "teamId": m.get("teamId")},
                    })
                return converted[:count]
        console.print("[dim]战队专用接口无数据，回退到扫秒模式...[/dim]")

    result = []
    today = date.today()
    scanned = 0
    console.print(f"[dim]扫秒历史比赛, 间隔{client.limiter.min_interval}秒[/dim]")

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), TaskProgressColumn(), console=console) as progress:
        task = progress.add_task("正在获取最近%d场已结束比赛..." % count, total=None)

        while len(result) < count and scanned < 30:
            d = today - timedelta(days=scanned)
            ds = d.strftime("%Y-%m-%d")
            progress.update(task, description="查询 %s (已有%d场)" % (ds, len(result)))
            data = client.request("GET", "/apg/eventcenter/csgo/getMatchList", {"matchTime": "%s 00:00:00" % ds})
            scanned += 1
            if not data:
                continue
            matches = extract_items(data)
            for m in matches:
                if m.get("status") == 3:
                    if team_id:
                        t1 = (m.get("team1DTO") or {}).get("teamId")
                        t2 = (m.get("team2DTO") or {}).get("teamId")
                        if str(t1) != str(team_id) and str(t2) != str(team_id):
                            continue
                    result.append(m)
                    if len(result) >= count:
                        break
            progress.update(task, advance=1)

    result.sort(key=lambda x: x.get("endTime") or x.get("startTime") or 0, reverse=True)
    console.print("[dim]已获取%d场[/dim]" % len(result))
    return result[:count]


def fetch_event_matches(client: APIClient, event_id: str) -> list:
    """获取赛事完整赛程"""
    from datetime import timedelta, date, datetime
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

    detail = client.request("GET", "/apg/eventcenter/csgo/event/getEventDetail", {"eventId": event_id})
    if not detail or detail.get("code") != 0:
        console.print("[red]获取赛事信息失败[/red]")
        return []

    evt = detail.get("result", {}).get("eventDetail", {})
    start_ts = evt.get("startTime")
    end_ts = evt.get("endTime")

    if not start_ts:
        console.print("[red]赛事暂无开始时间[/red]")
        return []

    start_date = date.fromtimestamp(start_ts / 1000)
    end_date = date.fromtimestamp(end_ts / 1000) if end_ts else start_date + timedelta(days=7)
    days = (end_date - start_date).days + 1

    console.print("[dim]赛事: %s | %s ~ %s | 共%d天[/dim]" % (evt.get("name", ""), start_date, end_date, days))

    result = []
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), TaskProgressColumn(), console=console) as progress:
        task = progress.add_task("获取赛程...", total=days)
        for i in range(days):
            d = start_date + timedelta(days=i)
            ds = d.strftime("%Y-%m-%d")
            progress.update(task, description="查询 %s" % ds)
            data = client.request("GET", "/apg/eventcenter/csgo/getMatchList", {"matchTime": "%s 00:00:00" % ds})
            progress.update(task, advance=1)
            if not data:
                continue
            matches = extract_items(data)
            for m in matches:
                if str(m.get("eventId")) == str(event_id):
                    result.append(m)

    result.sort(key=lambda x: x.get("startTime") or 0)
    console.print("[dim]共%d场比赛[/dim]" % len(result))
    return result


def extract_items(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        result = data.get("result") or data.get("data") or data.get("results") or data
        if isinstance(result, dict):
            for key in ("dtoList", "matchResponse", "eventResponse", "items", "list"):
                sub = result.get(key)
                if sub is not None:
                    return extract_items(sub)
            return [result]
        return result if isinstance(result, list) else [result]
    return [data]


def fetch_and_display(client: APIClient, apis: dict, args: argparse.Namespace):
    endpoints = apis.get("endpoints", {})
    if not endpoints:
        console.print("[red]API端点列表为空[/red]")
        return

    if args.list:
        console.print(f"\n[bold]发现 {len(endpoints)} 个API端点:[/bold]")
        table = Table(show_header=True, box=box.SIMPLE)
        table.add_column("#", style="dim")
        table.add_column("方法", width=6)
        table.add_column("路径")
        table.add_column("参数")
        for i, key in enumerate(sorted(endpoints.keys()), 1):
            info = endpoints[key]
            params = "&".join(f"{k}={v}" for k, v in info.get("query", {}).items())
            table.add_row(str(i), info["method"], info["path"], params)
        console.print(table)
        return

    filtered = {}
    if args.endpoint:
        for key, info in endpoints.items():
            if args.endpoint.lower() in key.lower() or args.endpoint.lower() in info["path"].lower():
                filtered[key] = info
    else:
        filtered = endpoints

    if not filtered:
        console.print(f"[red]未找到匹配 '{args.endpoint}' 的端点[/red]")
        return

    key = list(sorted(filtered.keys()))[0]
    info = filtered[key]

    params = dict(info.get("query", {}))
    if args.params:
        for p in args.params:
            if "=" in p:
                k, v = p.split("=", 1)
                params[k] = v

    page = getattr(args, "page", None)
    if page is not None:
        params["page"] = str(page)
    if getattr(args, "page_size", None):
        params["pageSize"] = str(args.page_size)
    if getattr(args, "page_num", None):
        params["pageNum"] = str(args.page_num)

    if "match" in key.lower() and "matchTime" in info.get("query", {}):
        date_val = getattr(args, "date", None)
        if date_val:
            params["matchTime"] = f"{date_val} 00:00:00"
        elif "matchTime" not in params:
            pass

    # Team detail mode: -e team -i <teamId>  or  --team <name> without -e match
    team_query_id = getattr(args, "id", None) or None
    team_query_name = getattr(args, "team", None) or None

    if team_query_id is not None and "team" in key.lower():
        team_data = client.request("GET", "/apg/eventcenter/csgo/team/detail", {"teamId": str(team_query_id)})
        if team_data and team_data.get("code") == 0:
            detail = team_data.get("result", {})
            if detail:
                display_team_detail(client, detail, args)
                return
        console.print(f"[yellow]未找到 teamId={team_query_id}[/yellow]")
        return

    if team_query_name is not None and "team" in key.lower() and "match" not in key.lower():
        resolved = resolve_team(client, team_query_name)
        if resolved:
            team_data = client.request("GET", "/apg/eventcenter/csgo/team/detail", {"teamId": str(resolved["teamId"])})
            if team_data and team_data.get("code") == 0:
                detail = team_data.get("result", {})
                if detail:
                    display_team_detail(client, detail, args)
                    return
        console.print(f"[yellow]未找到队伍 '{team_query_name}'[/yellow]")
        return

    # Player profile mode: -e player -i <playerId>  or  --player-name <name>
    player_query_id = getattr(args, "id", None) or None
    player_query_name = getattr(args, "player_name", None) or None

    if (player_query_id is not None or player_query_name is not None) and "player" in key.lower():
        pid = player_query_id
        if not pid and player_query_name:
            resolved = resolve_player(client, player_query_name)
            if resolved:
                pid = resolved["playerId"]
                console.print("[dim]匹配选手: %s (playerId=%s)[/dim]" % (resolved.get("playerName", ""), pid))
            else:
                console.print(f"[yellow]未找到选手 '{player_query_name}'[/yellow]")
                return
        if pid:
            display_player_profile(client, pid)
        else:
            console.print("[yellow]未提供选手 ID[/yellow]")
        return

    # Event schedule mode: -e event -i <eventId>
    # Use /event/ to avoid matching "eventcenter" in the key
    evt_id = getattr(args, "id", None) or None
    if evt_id is not None and "/event/" in key.lower():
        console.print("[bold]获取赛事赛程...[/bold]")
        matches = fetch_event_matches(client, str(evt_id))
        if not matches:
            return
        if args.pretty:
            display_pretty(matches, "GET /apg/eventcenter/csgo/getMatchList", {"result": matches}, args)
            return
        console.print(json.dumps(matches, ensure_ascii=False, indent=2))
        return

    # Major event mode: --spotlight
    spotlight = getattr(args, "spotlight", None)
    if spotlight:
        spotlight_evt = find_spotlight_event(client)
        if not spotlight_evt:
            console.print("[yellow]未找到进行中的赛事[/yellow]")
            return
        evt_id = spotlight_evt.get("eventId")

        if "match" in key.lower():
            matches = fetch_event_matches(client, str(evt_id))
            if not matches:
                return
            recent_n = getattr(args, "recent", None)
            if recent_n:
                finished = [m for m in matches if m.get("status") == 3]
                finished.sort(key=lambda x: x.get("endTime") or x.get("startTime") or 0, reverse=True)
                items = finished[:recent_n]
            else:
                status = getattr(args, "status", None)
                if status:
                    items = [m for m in matches if m.get("status") == status]
                else:
                    items = matches
            if not items:
                console.print("[yellow]无匹配比赛[/yellow]")
                return
            console.print("[dim]当前聚焦赛事: %s | 奖金: %s[/dim]" % (
                spotlight_evt.get("nameZh") or spotlight_evt.get("name", ""),
                spotlight_evt.get("prize", "")))
            if args.pretty:
                display_pretty(items, "GET /apg/eventcenter/csgo/getMatchList", {"result": items}, args)
                return
            console.print(json.dumps(items, ensure_ascii=False, indent=2))
            return

        if args.pretty:
            from rich.columns import Columns
            panel = Panel.fit("[bold yellow]%s[/bold yellow]\n奖金: %s" % (
                spotlight_evt.get("nameZh") or spotlight_evt.get("name", ""),
                spotlight_evt.get("prize", "")))
            console.print(panel)
            return
        console.print(json.dumps(spotlight_evt, ensure_ascii=False, indent=2))
        return

    # Recent matches mode: --recent N
    recent_n = getattr(args, "recent", None)
    if recent_n is not None and "match" in key.lower():
        team_name = getattr(args, "team", None) or None
        team_id = getattr(args, "team_id", None) or None
        if not team_id and team_name:
            resolved = resolve_team(client, team_name)
            if resolved:
                team_id = resolved["teamId"]
                console.print("[dim]匹配队伍: %s (teamId=%s)[/dim]" % (resolved["name"], team_id))
            else:
                console.print("[yellow]未找到队伍 '%s'[/yellow]" % team_name)
                return
        matches = fetch_recent_matches(client, count=recent_n, team_id=str(team_id) if team_id else None)
        if not matches:
            return
        if args.pretty:
            display_pretty(matches, key, {"result": matches}, args)
            return
        console.print(json.dumps(matches, ensure_ascii=False, indent=2))
        return

    match_id = getattr(args, "id", None) or None
    is_detail = match_id is not None and "match" in key.lower()

    if not is_detail:
        console.print(f"[bold]请求:[/bold] {info['method']} {info['path']}")
        if params:
            console.print(f"[bold]参数:[/bold] {params}")

    data = client.request(info["method"], info["path"], params)
    if not data:
        return

    if is_detail:
        detail_data = client.request("GET", "/apg/eventcenter/csgo/getMatchDetail", {"matchId": str(match_id)})
        if detail_data and detail_data.get("code") == 0:
            match_obj = detail_data.get("result", {}).get("match", {})
            if match_obj:
                if args.pretty:
                    display_match_detail(match_obj, args, client)
                    return
                if args.raw or args.format == "json":
                    console.print(json.dumps(detail_data, ensure_ascii=False, indent=2))
                    return
                data = detail_data
            else:
                console.print(f"[yellow]未找到 matchId={match_id}[/yellow]")
                return
        else:
            console.print(f"[yellow]查询 matchId={match_id} 失败[/yellow]")
            return

    history = getattr(args, "history", None)
    if history is not None and "event/" in key:
        days = history
        console.print(f"[bold]扫描近{days}天比赛记录提取历史赛事...[/bold]")
        items = fetch_event_history(client, days_back=days, min_matches=1)
        if not items:
            console.print("[yellow]未找到历史赛事[/yellow]")
            return
        status_val = getattr(args, "status", None)
        if status_val is not None:
            items = [e for e in items if e.get("status") == status_val]
            if not items:
                label = {1:"进行中",2:"即将",3:"已结束"}.get(status_val, str(status_val))
                console.print(f"[yellow]无{label}赛事[/yellow]")
                return
        data = {"result": items}
        if args.pretty:
            display_pretty(items, key, data, args)
            return

    if args.raw or args.format == "json":
        output_line = json.dumps(data, ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output_line)
            console.print(f"[green]已保存至 {args.output}[/green]")
        else:
            console.print(output_line)
        return

    items = extract_items(data)

    status = getattr(args, "status", None)
    if status is not None and items and isinstance(items[0], dict) and "status" in items[0]:
        items = [m for m in items if m.get("status") == status]
        if not items:
            label = {1: "进行中", 2: "即将开始", 3: "已结束"}.get(status, f"状态={status}")
            console.print(f"[yellow]无{label}项目[/yellow]")
            return

    team_name = getattr(args, "team", None) or None
    team_id = getattr(args, "team_id", None) or None
    if (team_name or team_id) and items and isinstance(items[0], dict):
        if not team_id and team_name:
            resolved = resolve_team(client, team_name)
            if resolved:
                team_id = resolved["teamId"]
                console.print("[dim]匹配队伍: %s[/dim]" % resolved["name"])
            else:
                console.print("[yellow]未找到队伍 '%s'[/yellow]" % team_name)
                return
        filtered = []
        for m in items:
            t1 = (m.get("team1DTO") or {}).get("teamId")
            t2 = (m.get("team2DTO") or {}).get("teamId")
            if str(t1) == str(team_id) or str(t2) == str(team_id):
                filtered.append(m)
        items = filtered
        if not items:
            console.print("[yellow]该队伍无匹配比赛[/yellow]")
            return

    if args.pretty:
        display_pretty(items, key, data, args)
        return

    if args.format == "csv":
        if items and isinstance(items[0], dict):
            output_path = args.output or f"output_{sanitize_filename(key)}.csv"
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=items[0].keys())
                writer.writeheader()
                writer.writerows(items)
            console.print(f"[green]已保存CSV ({len(items)}行) 至 {output_path}[/green]")
        else:
            console.print("[yellow]数据格式不适合CSV输出[/yellow]")
        return

    items_display = items[:args.limit]
    table = Table(show_header=True, header_style="bold", box=box.ROUNDED)

    if items_display and isinstance(items_display[0], dict):
        for col in list(items_display[0].keys())[:args.max_cols]:
            table.add_column(col)

        for item in items_display:
            row = []
            for col in [c.header for c in table.columns]:
                val = item.get(col, "")
                if isinstance(val, (dict, list)):
                    val = json.dumps(val, ensure_ascii=False)[:60]
                row.append(str(val)[:40])
            table.add_row(*row)

    if table.columns:
        console.print(table)
    else:
        console.print(json.dumps(items_display, ensure_ascii=False, indent=2))

    if len(items) > args.limit:
        console.print(f"[dim]... 共 {len(items)} 条, 显示前 {args.limit} 条[/dim]")

    if args.output and args.format != "csv":
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        console.print(f"[green]已保存至 {args.output}[/green]")


def build_parser():
    parser = argparse.ArgumentParser(
        description="完美世界CS电竞数据中心 - API数据获取工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  fetcher.py --list                   列出所有API端点
  fetcher.py -e match -p              人类友好模式查看比赛
  fetcher.py -e team -p               查看战队排名
  fetcher.py -e team -i 6667 -p       查看战队详情(阵容/排名/转会)
  fetcher.py -e event -p              人类友好模式查看赛事
  fetcher.py -e player -p             人类友好模式查看选手数据
  fetcher.py -e player -i 20447 -p    查看选手详情(按ID)
  fetcher.py -e player --player-name donk -p   查看选手详情(按名称)
  fetcher.py -e match -p              查看今天比赛
  fetcher.py -e match -p --status 3   查看今天已结束的比赛
  fetcher.py -e match -p --recent 10           最近10场已结束比赛
  fetcher.py -e match -p --recent 10 --team FaZe   FaZe最近10场
  fetcher.py -e match -p -d 2026-07-01         查看某天比赛
  fetcher.py -e match -i 2395575 -p            查看比赛详情（含选手数据）
  fetcher.py -e event -i 8914 -p               查看赛事完整赛程
  fetcher.py -e match -p -d 2026-07-01 --status 3  查看某天已结束比赛
  fetcher.py -e event --history 30 -p          查看近30天历史赛事
  fetcher.py --spotlight                           查看当前聚焦赛事
  fetcher.py -e match --spotlight --recent 10 -p   重要赛事最近10场
  fetcher.py -e match --spotlight --status 1 -p    重要赛事正在进行的比赛
  fetcher.py -e match --spotlight --status 2 -p    重要赛事即将开始的比赛
  fetcher.py -e match --raw                   输出原始JSON
  fetcher.py -e match --format csv            输出CSV
  fetcher.py -e match --limit 5               限制显示5条
  fetcher.py -e match -o result.json          保存到文件
  fetcher.py -e event pageNum=2               翻页
  fetcher.py --rate 2.0                       设置请求间隔2秒
        """,
    )
    parser.add_argument("-e", "--endpoint", help="按关键词筛选端点 (match / team / event / player)")
    parser.add_argument("--list", action="store_true", help="列出所有API端点")
    parser.add_argument("-p", "--pretty", action="store_true", help="人类友好输出（隐藏ID/链接等技术字段）")
    parser.add_argument("--raw", action="store_true", help="输出原始JSON")
    parser.add_argument("--format", choices=["json", "csv", "table"], default="table", help="输出格式")
    parser.add_argument("-i", "--id", help="指定ID查询详情 (matchId配-e match, eventId配-e event)")
    parser.add_argument("--recent", type=int, metavar="N", help="最近N场已结束比赛 (搭配 -e match --status 3 使用)")
    parser.add_argument("--team", help="按战队名筛选比赛 (搭配 -e match 使用)")
    parser.add_argument("--team-id", type=int, help="按战队ID精确筛选")
    parser.add_argument("--player-name", help="按选手游戏名查询 (搭配 -e player 使用)")
    parser.add_argument("--limit", type=int, default=20, help="表格最大行数")
    parser.add_argument("--max-cols", type=int, default=8, help="表格最大列数")
    parser.add_argument("--status", type=int, choices=[1, 2, 3], help="筛选比赛状态: 1=进行 2=即将 3=结束")
    parser.add_argument("--history", type=int, nargs="?", const=30, metavar="天数", help="从比赛记录中提取历史赛事(默认30天), 搭配 -e event 使用")
    parser.add_argument("--spotlight", action="store_true", help="当前聚焦赛事(进行中奖金最高的赛事)")
    parser.add_argument("-d", "--date", help="指定日期 (YYYY-MM-DD), 默认今天")
    parser.add_argument("-o", "--output", help="输出到文件")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE_LIMIT, help=f"请求间隔(秒)")
    parser.add_argument("params", nargs="*", help="URL参数如 page=1 pageSize=20")
    return parser


def main():
    import sys as _sys
    if hasattr(_sys.stdout, "reconfigure"):
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(_sys.stderr, "reconfigure"):
        _sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = build_parser()
    args = parser.parse_args()

    apis = load_apis()
    client = APIClient(apis, rate_limit=args.rate)

    console.print(Panel.fit("[bold cyan]完美世界CS电竞数据中心[/bold cyan]"))

    if args.spotlight and not args.endpoint:
        spotlight_evt = find_spotlight_event(client)
        if not spotlight_evt:
            console.print("[yellow]未找到进行中的赛事[/yellow]")
            return
        evt_id = spotlight_evt.get("eventId")
        prize = spotlight_evt.get("prize", "")
        name = spotlight_evt.get("nameZh") or spotlight_evt.get("name", "")
        console.print(Panel.fit(
            "[bold yellow]当前聚焦赛事[/bold yellow]\n"
            "[bold]%s[/bold]\n"
            "[dim]奖金: %s  |  eventId: %s[/dim]" % (name, prize, evt_id)
        ))
        return

    if args.list:
        fetch_and_display(client, apis, args)
        return

    if not args.endpoint:
        parser.print_help()
        console.print("\n[yellow]提示: --list 查看端点, -e <关键词> 获取数据[/yellow]")
        return

    fetch_and_display(client, apis, args)


if __name__ == "__main__":
    main()
