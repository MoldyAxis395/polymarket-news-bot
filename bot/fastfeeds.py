"""Fast structured sources, quicker than articles: official injury/lineup data and insiders.

Each source turns a CHANGE into a NewsItem (title written so the normal pipeline understands it):
  EspnInjuries   player status flips to Out / IR / Suspension, or back to Active (NFL NBA NHL MLB WNBA CFB)
  MlbLineups     player removed from an already posted lineup (late scratch)
  SoccerLineups  confirmed starting XI leaves out players who started the team's previous game
  Bluesky        new posts by insiders (Rapoport, Shams, Passan, Friedman, LeBrun, Romano...)
First poll of each source only records the current state (no flood of old news on start).
"""
import json
import time
from datetime import datetime, timedelta, timezone

import requests

from . import config
from .news import NewsItem

_s = requests.Session()
_s.headers.update({"User-Agent": "Mozilla/5.0"})  # ESPN answers 403 to the bot's custom UA
STATE = config.DATA / "fastfeeds.json"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/"
OUT = {"Out", "Injured Reserve", "Suspension", "Out For Season", "Injured List"}
_now = lambda: datetime.now(timezone.utc)  # noqa: E731


def _dt(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return _now()


def _when(s):
    """ESPN's update time if recent, else detection time (status changes don't always touch it)."""
    t = _dt(s)
    return t if timedelta(0) <= _now() - t <= timedelta(hours=1) else _now()


class _Source:
    name = ""
    every = 60

    def __init__(self, state, log):
        self.st = state.setdefault(self.name, {})
        self.log = log
        self.last = 0.0
        self.errors = 0

    def due(self):
        return time.time() - self.last >= self.every

    def poll(self):
        if not self.due():
            return []
        self.last = time.time()
        try:
            out = list(self._poll())
            self.errors = 0
            return out
        except Exception as e:
            self.errors += 1
            if self.errors in (1, 10, 100):
                self.log(f"{self.name} error x{self.errors}: {str(e)[:150]}")
            return []


class EspnInjuries(_Source):
    name = "espn_injuries"
    every = config.FAST_POLL_SEC["espn_injuries"]

    def _poll(self):
        for path in config.ESPN_INJURY_LEAGUES:
            r = _s.get(f"{ESPN}{path}/injuries", timeout=20)
            r.raise_for_status()
            known = self.st.setdefault(path, {})
            first = not known
            for team in r.json().get("injuries", []):
                for inj in team.get("injuries", []):
                    a = inj.get("athlete", {})
                    pid, new = str(a.get("id") or a.get("displayName")), inj.get("status", "")
                    old = known.get(pid)
                    known[pid] = new
                    if first or old == new:
                        continue
                    if new in OUT and old not in OUT:
                        verb = "ruled out"
                    elif new == "Active" and old in OUT | {"Doubtful"}:
                        verb = "cleared to play"
                    else:
                        continue
                    pos = (a.get("position") or {}).get("abbreviation", "")
                    tname = (a.get("team") or {}).get("displayName") or team.get("displayName", "")
                    league = path.split("/")[1]
                    yield NewsItem(
                        self.name, f"{a.get('displayName')} ({pos}, {tname}) {verb}: {old or 'healthy'} -> {new} [{league.upper()}]",
                        inj.get("longComment") or inj.get("shortComment") or "", "",
                        _when(inj.get("date", "")), "ESPN")


class MlbLineups(_Source):
    name = "mlb_lineups"
    every = config.FAST_POLL_SEC["mlb_lineups"]

    def _poll(self):
        day = _now() - timedelta(hours=6)  # MLB dates are US-local
        r = _s.get("https://statsapi.mlb.com/api/v1/schedule", timeout=20, params={
            "sportId": 1, "startDate": f"{day:%Y-%m-%d}", "endDate": f"{day + timedelta(days=1):%Y-%m-%d}",
            "hydrate": "lineups"})
        r.raise_for_status()
        for d in r.json().get("dates", []):
            for g in d.get("games", []):
                if g.get("status", {}).get("abstractGameState") != "Preview":
                    continue
                for side, key in (("home", "homePlayers"), ("away", "awayPlayers")):
                    players = {str(p["id"]): p.get("fullName", "") for p in g.get("lineups", {}).get(key, [])}
                    if not players:
                        continue
                    gid = f"{g['gamePk']}-{side}"
                    before = self.st.get(gid)
                    self.st[gid] = players
                    if not before:
                        continue
                    team = g["teams"][side]["team"]["name"]
                    opp = g["teams"]["away" if side == "home" else "home"]["team"]["name"]
                    for pid in set(before) - set(players):
                        yield NewsItem(self.name, f"{before[pid]} scratched, ruled out of {team} lineup vs {opp} [MLB]",
                                       "Removed from the posted starting lineup.", "", _now(), "MLB.com")


class SoccerLineups(_Source):
    name = "soccer_lineups"
    every = config.FAST_POLL_SEC["soccer_lineups"]

    def _poll(self):
        xi = self.st.setdefault("xi", {})        # team id -> starters of its last game
        done = self.st.setdefault("done", {})    # event id -> unix time lineup was handled
        now = _now()
        for lg in config.ESPN_SOCCER_LEAGUES:
            r = _s.get(f"{ESPN}soccer/{lg}/scoreboard", timeout=20)
            r.raise_for_status()
            for ev in r.json().get("events", []):
                start = _dt(ev.get("date", ""))
                if ev["id"] in done or not (timedelta(0) < start - now < timedelta(minutes=90)):
                    continue
                s = _s.get(f"{ESPN}soccer/{lg}/summary", params={"event": ev["id"]}, timeout=20).json()
                teams = [(t["team"], [p["athlete"]["displayName"] for p in t.get("roster", []) if p.get("starter")])
                         for t in s.get("rosters", [])]
                if len(teams) != 2 or any(len(st) < 11 for _, st in teams):
                    continue  # not published yet
                done[ev["id"]] = time.time()
                for i, (team, starters) in enumerate(teams):
                    prev = xi.get(team["id"])
                    xi[team["id"]] = starters
                    missing = [p for p in prev or [] if p not in starters]
                    if not missing:
                        continue
                    opp = teams[1 - i][0]["displayName"]
                    yield NewsItem(self.name, f"{team['displayName']} confirmed lineup vs {opp}: "
                                   f"{', '.join(missing)} ruled out of starting XI [{lg}]",
                                   f"Started previous game, not in today's XI. Starting XI: {', '.join(starters)}",
                                   "", now, "ESPN")
        cutoff = time.time() - 3 * 86400
        self.st["done"] = {k: t for k, t in done.items() if t > cutoff}


class Bluesky(_Source):
    name = "bluesky"
    every = config.FAST_POLL_SEC["bluesky"]

    def _poll(self):
        for handle, who in config.BLUESKY_ACCOUNTS.items():
            r = _s.get("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed", timeout=15,
                       params={"actor": handle, "limit": 10, "filter": "posts_no_replies"})
            r.raise_for_status()
            last = self.st.get(handle)
            posts = [f["post"] for f in r.json().get("feed", []) if "reason" not in f]  # skip reposts
            if not posts:
                continue
            self.st[handle] = max(p["record"]["createdAt"] for p in posts)
            if last is None:
                continue
            for p in posts:
                rec = p["record"]
                if rec["createdAt"] <= last:
                    continue
                text = " ".join(rec.get("text", "").split())
                yield NewsItem(self.name, text[:280], text[280:680], "", _dt(rec["createdAt"]), who)


class FastFeeds:
    def __init__(self, log):
        self.state = {}
        if STATE.exists():
            try:
                self.state = json.loads(STATE.read_text())
            except Exception:
                pass
        self.sources = [cls(self.state, log) for cls in (EspnInjuries, MlbLineups, SoccerLineups, Bluesky)
                        if cls.name in config.FAST_FEEDS]

    def poll(self):
        due = [s for s in self.sources if s.due()]
        if not due:
            return []
        items = [it for s in due for it in s.poll()]
        STATE.write_text(json.dumps(self.state))
        return items
