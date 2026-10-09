"""BOT PENGWIN: copy-trade Kristian Pengwin's football picks (mondopengwin.it) on Polymarket.

Separate paper portfolio (data/pengwin/), own Telegram prefix, runs in a background thread.
  1. every PENGWIN_POLL_SEC read the league pages, collect match pages
  2. re-read each match page until "Analisi e pronostico di Kristian Pengwin:" has the pick
     (published ~24 h before kick-off)
  3. find the Polymarket event (Italian team names -> LLM picks among search results if needed)
  4. LLM maps the pick to ONE market + outcome of that event (exact or closest main leg)
  5. buy PENGWIN_STAKE and hold until the market settles
"""
import html
import json
import re
import threading
import time
import traceback
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

from . import config, polymarket as pm
from .brain import Signal
from .matcher import tokens
from .notify import telegram
from .paper import Paper

DIR = config.DATA / "pengwin"
DIR.mkdir(exist_ok=True)
PAGES = DIR / "pages.json"
STATUS = DIR / "status.json"
BASE = "https://www.mondopengwin.it/pronostici/calcio/"
LABEL = "🐧 BOT PENGWIN\n"
_s = requests.Session()
_s.headers.update({"User-Agent": "Mozilla/5.0"})
MATCH_LINK = re.compile(r'href="(https://www\.mondopengwin\.it/pronostici/calcio/[a-z0-9-]+/[a-z0-9-]+-pronostico[a-z0-9-]*)"')
PICK = re.compile(r"Analisi e pronostico di Kristian Pengwin:(.*?)Per non perderti", re.S)
TITLE = re.compile(r"<title>\s*(.*?)\s*:\s*STATISTICHE", re.S | re.I)
# club prefixes/suffixes that differ between the Italian site and Polymarket
NOISE = {"fc", "cf", "ac", "as", "ss", "sc", "afc", "calcio", "club", "de", "del"}


def _text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _toks(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()  # Málaga -> Malaga
    return set(tokens(s.replace("-", " "))) - NOISE


class Pengwin:
    def __init__(self, brain, log):
        self.ask = getattr(brain, "_ask", None)  # needs the LLM brain
        self.log = lambda m: log(f"[pengwin] {m}")
        self.paper = Paper(self.log, state=DIR / "portfolio.json", trades=DIR / "trades.csv", label=LABEL,
                           start_cash=config.PENGWIN_START_CASH, per_trade=config.PENGWIN_STAKE,
                           hold_to_resolution=True, news_label="Pronostico")
        self.pages = json.loads(PAGES.read_text(encoding="utf-8")) if PAGES.exists() else {}
        self.stop_at = float("inf")

    # ---------- scraping ----------
    def _get(self, url):
        r = _s.get(url, timeout=25)
        r.raise_for_status()
        time.sleep(1)  # be polite
        return r.text

    def scan(self):
        now = time.time()
        for league in config.PENGWIN_LEAGUES:
            try:
                for url in dict.fromkeys(MATCH_LINK.findall(self._get(BASE + league + "/"))):
                    self.pages.setdefault(url, {"first": now, "status": "pending"})
            except Exception as e:
                self.log(f"league {league}: {e}")
        cutoff = now - 5 * 86400
        for url, pg in list(self.pages.items()):
            if pg["status"] != "pending":
                continue
            if pg["first"] < cutoff:
                pg["status"] = "expired"
                continue
            try:
                page = self._get(url)
            except Exception as e:
                self.log(f"page {url[-60:]}: {e}")
                continue
            m = PICK.search(page)
            pick = _text(m.group(1)) if m else ""
            if not pick or "Non ancora disponibile" in pick:
                continue
            t = TITLE.search(page)
            pg.update(status="picked", pick=pick, match=_text(t.group(1)) if t else url.rsplit("/", 1)[1])
            self.handle(url, pg)
        cutoff = now - 30 * 86400
        self.pages = {u: p for u, p in self.pages.items() if p["first"] > cutoff}
        PAGES.write_text(json.dumps(self.pages, ensure_ascii=False, indent=0), encoding="utf-8")

    # ---------- matching ----------
    def find_event(self, match):
        """Polymarket match event for 'ARSENAL-LEEDS UNITED' (dict) or None."""
        now = datetime.now(timezone.utc)
        found = {}
        for q in [match.replace("-", " ")] + [w for w in re.split(r"[-\s]+", match) if len(w) >= 4]:
            for ev in pm.search_events(q):
                end = pm._parse_dt(ev.get("endDate"))
                if (" vs. " in ev.get("title", "") and "More Markets" not in ev["title"] and end
                        and now - timedelta(minutes=10) < end < now + timedelta(days=4)):
                    found[ev["slug"]] = ev
        mt = _toks(match)
        both = [ev for ev in found.values()
                if all(_toks(side) & mt for side in ev["title"].split(" vs. ", 1))]
        if len(both) == 1:
            return both[0]
        cands = both or list(found.values())
        if not cands or not self.ask:
            return None
        cands = cands[:15]
        lines = "\n".join(f"{i}) {ev['title']} ({ev['endDate'][:16]})" for i, ev in enumerate(cands))
        d = self.ask(f"Italian betting site match name: \"{match}\" (Italian team names, home-away).\n"
                     f"Polymarket events:\n{lines}\n\nWhich event is the SAME football match? "
                     'Answer JSON only: {"event": i} or {"event": null}.')
        try:
            return cands[int(d["event"])] if d and d.get("event") is not None else None
        except (ValueError, IndexError, TypeError, KeyError):
            return None

    def map_pick(self, match, pick, markets):
        lines = "\n".join(f"{i}) {m.question} | " + ", ".join(f"{j}={o} @{p:.2f}" for j, (o, p) in
                                                             enumerate(zip(m.outcomes, m.prices)))
                          for i, m in enumerate(markets))
        return self.ask(
            f"A football tipster (Italian) gives this pick for {match}:\n\"{pick[-700:]}\"\n\n"
            f"Polymarket markets for this match (outcome=price):\n{lines}\n\n"
            "Choose the ONE market + outcome that replicates the pick. Italian betting terms: 1=home win, "
            "X=draw, 2=away win, 1X/X2/12=double chance, GG/Goal=both teams score, NG/No Goal=not both, "
            "Over/Under N=total goals, 'casa'=home team, 'ospite/trasferta'=away team, Multigol A-B = "
            "goals between A and B, 'combo' = several legs.\n"
            "Rules:\n- exact: same bet (e.g. X2 = NO on 'Will <home> win?', 1 = YES on 'Will <home> win?', "
            "GG = YES on Both Teams to Score).\n- approx: the pick is a combo/multigol/line Polymarket lacks; "
            "take ONE of its legs, same team, same period (full match vs 1st half), same direction, same "
            "or nearest line (e.g. 'Multigol 2-5 casa' -> <home> O/U 1.5 Over; '1X + Under 3.5' -> NO on "
            "'Will <away> win?'; 'casa over 0.5 primo tempo' -> <home> 1st Half O/U 0.5 Over).\n"
            "- NEVER a different bet type, team or period than a leg of the pick (an 'X or Under 2.5' pick "
            "is NOT a 1st-half under).\n- shots (tiri), corners, cards, scorers, or no leg available -> market null.\n"
            'Answer JSON only: {"market": i, "outcome": j, "match": "exact|approx", '
            '"pick": "the bet in short, e.g. MULTIGOL 2-5 CASA @1.45", "why": "short"} '
            'or {"market": null, "pick": "...", "why": "short"}.', effort="medium")

    def handle(self, url, pg):
        match, pick = pg["match"], pg["pick"]
        if not self.ask:
            pg.update(status="skipped", why="no LLM")
            return
        ev = self.find_event(match)
        if not ev:
            pg.update(status="skipped", why="no Polymarket event")
            self.log(f"skip {match}: no Polymarket event")
            return
        markets = pm.event_markets(ev["slug"]) + pm.event_markets(ev["slug"] + "-more-markets")
        markets = [m for m in markets if m.liquidity >= config.PENGWIN_MIN_LIQUIDITY and "2nd Half" not in m.question]
        d = self.map_pick(match, pick, markets) if markets else None
        short = (d or {}).get("pick") or pick[-120:]
        if not d or d.get("market") is None:
            return self.skip(pg, match, short, (d or {}).get("why", "no market"))
        try:
            m, j = markets[int(d["market"])], int(d["outcome"])
            assert j in (0, 1)
        except (ValueError, IndexError, KeyError, TypeError, AssertionError):
            return self.skip(pg, match, short, f"bad LLM answer {d}")
        why = self.paper.can_open(m.id)
        if not why:
            bid, ask = pm.best_bid_ask(m.token_ids[j])
            if bid is None or ask is None:
                why = "empty book"
            elif ask - bid > config.PENGWIN_MAX_SPREAD:
                why = f"spread {ask - bid:.3f}"
            elif not (config.PENGWIN_PRICE_MIN <= ask <= config.PENGWIN_PRICE_MAX):
                why = f"ask {ask} out of range"
            elif m.minutes_to_end() < 10:
                why = "match started"
        if why:
            return self.skip(pg, match, short, f"{m.question} -> {m.outcomes[j]}: {why}")
        tag = d.get("match", "approx")
        sig = Signal(m, j, 1.0, f"pengwin {tag}: {str(d.get('why', ''))[:150]}")
        if self.paper.open(sig, f"{match}: {short} ({'esatto' if tag == 'exact' else 'approssimato'})"):
            pg.update(status="traded", market=m.question, outcome=m.outcomes[j], map=tag)

    def skip(self, pg, match, short, why):
        pg.update(status="skipped", why=why)
        self.log(f"skip {match} [{short}]: {why}")
        telegram(f"{LABEL}⚪ SALTATO (paper)\n{match}\nPronostico: {short}\nMotivo: {why}")

    # ---------- loop ----------
    def write_status(self):
        p = self.paper
        STATUS.write_text(json.dumps({
            "updated": datetime.now().isoformat(timespec="seconds"), "start": p.start_cash,
            "cash": round(p.cash, 2), "equity": round(p.equity(), 2), "open": p.positions,
            "closed_count": len(p.closed), "realized_pnl": round(sum(c["pnl"] for c in p.closed), 2),
            "pages": {s: sum(1 for x in self.pages.values() if x["status"] == s)
                      for s in ("pending", "picked", "traded", "skipped", "expired")}}, indent=1))

    def run(self):
        last_scan = 0.0
        while time.time() < self.stop_at:
            try:
                if time.time() - last_scan > config.PENGWIN_POLL_SEC:
                    last_scan = time.time()
                    self.scan()
                self.paper.check_exits()
                self.write_status()
            except Exception:
                self.log("ERROR " + traceback.format_exc().strip().replace("\n", " | ")[-600:])
            time.sleep(60)

    def start(self, stop_at):
        self.stop_at = stop_at
        t = threading.Thread(target=self.run, name="pengwin", daemon=True)
        t.start()
        return t
