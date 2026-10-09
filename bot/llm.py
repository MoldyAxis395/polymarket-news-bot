"""LLM brain: OpenAI-compatible chat APIs (backends in config.LLM_BACKENDS).

Flow per headline (each step can reject; a rejection costs no further calls):
  1. keyword gate + FLUFF filter (previews, fantasy, rumours, "could/might", timelines...)
  2. LLM extracts the FACT: who, which team/entity, league, is it confirmed / new / a starter,
     good or bad for that team. Not confirmed, not new or not a key player -> stop.
  3. source check: a trusted publisher is enough; otherwise wait for a second, different
     publisher reporting the same person/entity within CORROBORATE_MIN
  4. markets are searched by the extracted team (not by headline words) and filtered by league
  5. LLM picks market + outcome; for team-vs-team / "Will X win" markets the side is checked
     deterministically against the fact's good/bad effect
  6. a second, skeptical LLM call (more reasoning) must approve the trade
Falls back to the rule brain when the API fails.
"""
import json
import os
import re
import time
from datetime import datetime, timezone

import requests

from . import config
from .brain import NEG, POS, Signal, _count
from .matcher import LEAGUE_RE, tokens

USAGE = config.DATA / "llm_usage.json"
# Only headlines that sound like a concrete market-moving event reach the LLM
GATE = NEG + POS + [
    "ceasefire", "verdict", "acquire", "acquires", "merger", "bankrupt", "bankruptcy", "tariff",
    "rate cut", "rate hike", "impeach", "impeached", "invades", "invasion", "launches", "strikes",
]
FLUFF_RE = re.compile(config.FLUFF, re.I)
KINDS = {"ruled_out", "suspended", "returns", "result", "resignation", "official_decision"}
WIN_Q = re.compile(r"^will (?:the )?(.+?) win\b", re.I)


class LLMBrain:
    name = "llm"

    def __init__(self, fallback, log=print):
        self.fallback = fallback
        self.log = log
        self.fallbacks = 0   # headlines handed to the rule brain because every backend failed
        self.errors = {}     # backend name -> last error, logged only when it changes
        self.cooldown = {}   # backend name -> unix time until which it is skipped
        self.last_backend = None
        self.recent = []     # facts from untrusted publishers waiting for a 2nd source: (t, publisher, keys)
        self.rejects = {}    # step -> count, shown in status.json
        self.usage = {}      # {"day": "YYYY-MM-DD", "<backend>": calls}
        if USAGE.exists():
            try:
                self.usage = json.loads(USAGE.read_text())
            except Exception:
                pass
        self.backends = [b for b in config.LLM_BACKENDS if not b.get("key_env") or os.environ.get(b["key_env"])]
        missing = [b["name"] for b in config.LLM_BACKENDS if b not in self.backends]
        if missing:
            self.log(f"WARNING llm backends without API key (skipped): {', '.join(missing)}")
        if not self.backends:
            self.log("WARNING no LLM backend available: every headline goes to the rule brain")

    # ---------- plumbing ----------
    def _today(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _count_call(self, name):
        if self.usage.get("day") != self._today():
            self.usage = {"day": self._today()}
        self.usage[name] = self.usage.get(name, 0) + 1
        USAGE.write_text(json.dumps(self.usage))

    def _err(self, name, msg):
        if self.errors.get(name) != msg:
            self.log(f"  llm {name}: {msg}")
            self.errors[name] = msg

    def stats(self):
        return {"backends": [b["name"] for b in self.backends], "fallbacks": self.fallbacks,
                "errors": self.errors, "rejects": self.rejects,
                "calls": {k: v for k, v in self.usage.items() if k != "day"}}

    def calls_today(self):
        if self.usage.get("day") != self._today():
            return 0
        return sum(v for k, v in self.usage.items() if k != "day")

    def _ask(self, prompt, effort=None):
        """Try backends in order (fast cloud first, local last). Parsed JSON dict, or None."""
        for b in self.backends:
            name = b["name"]
            if time.time() < self.cooldown.get(name, 0):
                continue
            if self.usage.get("day") == self._today() and self.usage.get(name, 0) >= b["daily_cap"]:
                continue
            headers = {"Authorization": f"Bearer {os.environ[b['key_env']]}"} if b.get("key_env") else {}
            extra = dict(b.get("extra", {}))
            if effort and "reasoning_effort" in extra:
                extra["reasoning_effort"] = effort
            try:
                r = requests.post(b["url"], timeout=b.get("timeout", 60), headers=headers, json={
                    "model": b["model"], "temperature": 0, "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}], **extra})
            except requests.exceptions.RequestException as e:
                self._err(name, f"unreachable ({type(e).__name__}), skip 5 min")
                self.cooldown[name] = time.time() + 300  # down/unreachable: skip for 5 min
                continue
            self._count_call(name)
            if r.status_code == 429:  # rate limited: skip until it resets
                try:
                    wait = float(r.headers.get("retry-after", 60))
                except ValueError:
                    wait = 60
                self._err(name, f"rate limited, skip {wait:.0f}s")
                self.cooldown[name] = time.time() + wait
                continue
            try:
                r.raise_for_status()
                txt = r.json()["choices"][0]["message"]["content"]
                self.last_backend = name
                out = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
                self.errors.pop(name, None)
                return out
            except Exception as e:
                self._err(name, f"HTTP {r.status_code} / bad answer: {str(e)[:120]}")
                continue
        return None

    def _reject(self, step, item, why):
        self.rejects[step] = self.rejects.get(step, 0) + 1
        self.log(f"  drop[{step}] {item.title[:90]} ({item.publisher}) :: {str(why)[:120]}")
        return []

    # ---------- brain API ----------
    def worth_asking(self, item):
        if FLUFF_RE.search(item.title):
            self.rejects["fluff"] = self.rejects.get("fluff", 0) + 1
            return False
        return bool(_count(item.title, GATE))

    def _fact(self, item):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._ask(
            f"Today is {today}. Breaking news headline from {item.publisher or item.source}:\n"
            f"\"{item.title}\"\n{item.summary[:300]}\n\n"
            "Extract the single concrete FACT this headline reports. Use your knowledge of current "
            "rosters: the team must be the one the person plays for NOW.\n"
            'Answer JSON only: {"kind": "ruled_out|suspended|returns|result|resignation|official_decision|other", '
            '"person": "full name or empty", "team": "team/country/company/party directly affected, full name", '
            '"league": "nfl|cfb|nba|wnba|nhl|mlb|soccer|tennis|politics|business|crypto|other", '
            '"key_player": true/false, "confirmed": true/false, "new": true/false, '
            '"effect": "good|bad" (for team), "why": "short"}\n'
            "kind: ruled_out = will miss a game or more (ruled out, scratched, week-to-week, out N weeks, "
            "placed on IR, season-ending); returns = cleared/back from absence; result = final score/outcome; "
            "official_decision = ruling, policy, deal, appointment; other = anything else.\n"
            "key_player: a regular starter / star / the person in charge. False for backups, prospects, "
            "youth, depth, already long-term injured players.\n"
            "confirmed: officially announced or reported as fact (ruled out, placed on IR, signed, "
            "resigned, final score). False for questionable, game-time decision, doubtful, expected, "
            "predicted, rumour, 'concern', 'scare', anything hedged.\n"
            "new: the event happened in the last ~24h. False for recovery/timeline updates, "
            "'still out', season-long absences, recaps and retrospectives.\n"
            "effect: careful with wording, e.g. 'boost with injury update' = player returning = good; "
            "'X ruled out' = bad for X's team.")

    def _corroborated(self, item, fact):
        """Trusted publisher, or another publisher already reported the same person/team."""
        keys = set(tokens(fact.get("person") or "")) or set(tokens(fact.get("team") or ""))
        now = time.time()
        self.recent = [r for r in self.recent if now - r[0] < config.CORROBORATE_MIN * 60]
        pub = (item.publisher or item.source).lower()
        match = any(p != pub and k & keys for _, p, k in self.recent)
        if keys:
            self.recent.append((now, pub, keys))
        return item.trusted or match

    def _markets(self, fact, index):
        names = [n for n in (fact.get("team"), (fact.get("team") or "").split(" ")[-1]) if n]
        league = (fact.get("league") or "").lower()
        out = []
        for m, s, t in index.search(names):
            slug_league = m.slug.split("-", 1)[0]
            if slug_league in LEAGUE_RE and league in LEAGUE_RE and slug_league != league:
                continue  # Jets (NFL) news vs Jets (NHL) market
            if slug_league in LEAGUE_RE and league == "soccer":
                continue
            out.append((m, s, t))
        return out

    def _side_ok(self, m, j, fact):
        """Deterministic check of the chosen side. True/False, or None when it can't tell."""
        team = set(tokens(fact.get("team") or ""))
        good = fact.get("effect") == "good"
        if not m.is_binary_yes_no:
            hits = [i for i, o in enumerate(m.outcomes) if set(tokens(o)) and set(tokens(o)) <= team]
            if len(hits) != 1:
                return False  # market isn't team-vs-team for this team
            return j == (hits[0] if good else 1 - hits[0])
        q = WIN_Q.match(m.question)
        if q and set(tokens(q.group(1))) & team:
            return j == (0 if good else 1)
        if re.search(r"\bdraw\b", m.question, re.I):
            return False  # one injury doesn't move a draw market predictably
        return None

    def analyze(self, item, candidates, index=None):
        fact = self._fact(item)
        if fact is None:  # API down: rule brain with its own strict matching (and trusted source only)
            self.fallbacks += 1
            if not item.trusted:
                return []
            strict = index.candidates(item.title) if index is not None else []
            return self.fallback.analyze(item, strict)
        if fact.get("kind") not in KINDS:
            return self._reject("kind", item, fact.get("kind"))
        if not fact.get("confirmed") or not fact.get("new"):
            return self._reject("unconfirmed", item, fact.get("why"))
        if fact.get("person") and not fact.get("key_player"):
            return self._reject("not-key", item, f"{fact.get('person')}: {fact.get('why')}")
        # LLM rosters go stale (Barkley -> Giants on an "Eagles" headline): the team must be named
        if not set(tokens(fact.get("team") or "")) & set(tokens(f"{item.title} {item.summary}")):
            return self._reject("team-not-named", item, fact.get("team"))
        if not self._corroborated(item, fact):
            return self._reject("1-source", item, f"{fact.get('person') or fact.get('team')}, wait 2nd source")

        cands = self._markets(fact, index) if index is not None else []
        if not cands:  # fall back to headline-matched markets that mention the team
            team = set(tokens(fact.get("team") or ""))
            cands = [c for c in candidates if team & c[2]]
        if not cands:
            return self._reject("no-market", item, fact.get("team"))

        now = datetime.now(timezone.utc)
        lines = []
        for i, (m, _, _) in enumerate(cands):
            end = f"{(m.end - now).total_seconds() / 3600:.0f}h" if m.end else "?"
            league = m.slug.split("-", 1)[0]
            outs = ", ".join(f"{j}={o} @{p:.2f}" for j, (o, p) in enumerate(zip(m.outcomes, m.prices)))
            lines.append(f"{i}) [{league}] {m.question} | ends in {end} | {outs}")
        fact_txt = (f"{fact.get('kind')}: {fact.get('person') or '-'} ({fact.get('team')}, {fact.get('league')}), "
                    f"{fact.get('effect')} for {fact.get('team')}. {fact.get('why', '')}")
        d = self._ask(
            f"You trade Polymarket on breaking news. Headline ({item.publisher}, {item.age_min:.0f} min ago):\n"
            f"\"{item.title}\"\nExtracted fact: {fact_txt}\n\n"
            "Candidate markets (outcome=price, price = implied probability):\n" + "\n".join(lines) +
            "\n\nPick the market whose price this fact should move by 5+ points soon: same team, same "
            "league, the game/event the fact affects (the next game, not a later one). Skip outcomes "
            "already near 0 or 1. confidence = probability the price moves your way by 5+ points.\n"
            'Answer JSON only: {"market": i, "outcome": j, "confidence": 0.0-1.0, "reason": "short"} '
            'where outcome j should RISE, or {"market": null, "reason": "short"}.')
        if not d or d.get("market") is None:
            return self._reject("no-pick", item, (d or {}).get("reason"))
        try:
            m = cands[int(d["market"])][0]
            j = int(d["outcome"])
            conf = float(d.get("confidence", 0))
        except (ValueError, IndexError, KeyError, TypeError):
            return []
        if j not in (0, 1):
            return []
        side = self._side_ok(m, j, fact)
        if side is False:
            return self._reject("side", item, f"{m.question} -> {m.outcomes[j]} vs fact {fact_txt}")
        if conf < config.MIN_CONFIDENCE:  # don't spend a verify call on a sub-threshold signal
            return [Signal(m, j, conf, f"llm[{self.last_backend}]: " + str(d.get("reason", ""))[:200])]

        v = self._ask(
            f"Today is {now:%Y-%m-%d}. Be a skeptical risk manager. A bot wants to BUY "
            f"\"{m.outcomes[j]}\" @ {m.prices[j]:.2f} in the Polymarket market \"{m.question}\" because of "
            f"this headline ({item.publisher}):\n\"{item.title}\"\nIts reading: {fact_txt}\n\n"
            "Check each point, answer false if ANY fails or you are unsure:\n"
            "1. the person really plays for / belongs to that team now, in that league\n"
            "2. the headline really says what the reading says (direction good/bad is right)\n"
            "3. it is new (not a known long-term absence, not a recap, not a rumour)\n"
            "4. the person matters enough to move this market 5+ points (starter/star, not depth)\n"
            "5. buying this outcome is the right side and the market is about the affected event\n"
            'Answer JSON only: {"ok": true/false, "why": "short"}', effort="medium")
        if not v or not v.get("ok"):
            return self._reject("verify", item, (v or {}).get("why", "no answer"))
        src = "trusted" if item.trusted else "2 sources"
        return [Signal(m, j, conf, f"llm[{self.last_backend}] {src}: " + str(d.get("reason", ""))[:200])]
