"""LLM brain: OpenAI-compatible chat APIs (backends in config.LLM_BACKENDS).

Flow per headline:
  1. cheap keyword gate (only headlines that sound like market-moving events)
  2. loose candidate markets from the index
  3. if none: ask the LLM which teams/people/countries are affected, search again
     (e.g. "Messi ruled out" -> Argentina)
  4. ask the LLM which outcome to buy, if any
Falls back to the rule brain when the API fails.
"""
import json
import os
import time
from datetime import datetime, timezone

import requests

from . import config
from .brain import NEG, POS, Signal, _count

USAGE = config.DATA / "llm_usage.json"
# Only headlines that sound like a concrete market-moving event reach the LLM
GATE = NEG + POS + [
    "ceasefire", "verdict", "acquire", "acquires", "merger", "bankrupt", "bankruptcy", "tariff",
    "rate cut", "rate hike", "impeach", "impeached", "invades", "invasion", "launches", "strikes",
]
# Headlines worth an extra "who is affected?" call when no market matched by name
EXTRACT_GATE = NEG + ["will play", "returns", "cleared", "upgraded", "signs", "endorses"]


class LLMBrain:
    name = "llm"

    def __init__(self, fallback, log=print):
        self.fallback = fallback
        self.log = log
        self.fallbacks = 0   # headlines handed to the rule brain because every backend failed
        self.errors = {}     # backend name -> last error, logged only when it changes
        self.cooldown = {}   # backend name -> unix time until which it is skipped
        self.last_backend = None
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
                "errors": self.errors, "calls": {k: v for k, v in self.usage.items() if k != "day"}}

    def calls_today(self):
        if self.usage.get("day") != self._today():
            return 0
        return sum(v for k, v in self.usage.items() if k != "day")

    def _ask(self, prompt):
        """Try backends in order (fast cloud first, local last). Parsed JSON dict, or None."""
        for b in self.backends:
            name = b["name"]
            if time.time() < self.cooldown.get(name, 0):
                continue
            if self.usage.get("day") == self._today() and self.usage.get(name, 0) >= b["daily_cap"]:
                continue
            headers = {"Authorization": f"Bearer {os.environ[b['key_env']]}"} if b.get("key_env") else {}
            try:
                r = requests.post(b["url"], timeout=b.get("timeout", 60), headers=headers, json={
                    "model": b["model"], "temperature": 0, "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}], **b.get("extra", {})})
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

    # ---------- brain API ----------
    def worth_asking(self, item):
        return bool(_count(item.title, GATE))

    def analyze(self, item, candidates, index=None):
        if not candidates and index is not None and _count(item.title, EXTRACT_GATE):
            ents = self._ask(
                "Breaking news headline:\n"
                f"\"{item.title}\"\n{item.summary[:300]}\n\n"
                "Which teams, countries, people, companies or parties could have a prediction market "
                "whose odds this changes? Include the team/country a mentioned player belongs to. "
                'Answer JSON only: {"entities": ["...", "..."]} (max 5, most relevant first, '
                'short common names e.g. "Argentina", "Chicago Bears", "Bears").')
            names = (ents or {}).get("entities") or []
            if names:
                candidates = index.search(names)
        if not candidates:
            return []

        now = datetime.now(timezone.utc)
        lines = []
        for i, (m, _, _) in enumerate(candidates):
            end = f"{(m.end - now).total_seconds() / 3600:.0f}h" if m.end else "?"
            league = m.slug.split("-", 1)[0]
            outs = ", ".join(f"{j}={o} @{p:.2f}" for j, (o, p) in enumerate(zip(m.outcomes, m.prices)))
            lines.append(f"{i}) [{league}] {m.question} | ends in {end} | {outs}")
        d = self._ask(
            "You trade Polymarket on breaking news: buy right after news, sell minutes/hours later "
            "when the crowd reprices. Headline (published "
            f"{item.age_min:.0f} min ago, source {item.source}):\n\"{item.title}\"\n{item.summary[:300]}\n\n"
            "Candidate markets (outcome=price, price = implied probability):\n" + "\n".join(lines) +
            "\n\nRules: pick a market ONLY if this headline is new information that should move that "
            "market's price by at least 5 points soon. Check the headline is about the SAME team/person/"
            "event and league as the market (same nickname in another sport = different team; know "
            "which team a player plays for). Ignore recaps of finished games, opinion pieces, previews "
            "without new facts, and outcomes already near 0 or 1.\n"
            "Qualifying news is a concrete, confirmed fact: a named STARTER/key player officially ruled out "
            "or suspended, a resignation, an official result, a confirmed decision. Answer null for: "
            "predicted or expected line-ups, 'injury concern/worry/bug', questionable/day-to-day tags, "
            "bench or depth players, injuries to players already known to be out, rumours, transfer talk, "
            "fantasy advice, and anything you would not bet your own money on. When unsure, answer null. "
            "confidence = probability the price moves your way by 5+ points; most real signals are 0.6-0.8.\n"
            'Answer JSON only: {"market": i, "outcome": j, "confidence": 0.0-1.0, "reason": "short"} '
            'where outcome j should RISE, or {"market": null, "reason": "short"}.')
        if d is None:  # API down: rule brain with its own strict matching
            self.fallbacks += 1
            strict = index.candidates(item.title) if index is not None else []
            return self.fallback.analyze(item, strict)
        if d.get("market") is None:
            return []
        try:
            m = candidates[int(d["market"])][0]
            j = int(d["outcome"])
            if j not in (0, 1):
                return []
            return [Signal(m, j, float(d.get("confidence", 0)),
                           f"llm[{self.last_backend}]: " + str(d.get("reason", ""))[:200])]
        except (ValueError, IndexError, KeyError, TypeError):
            return []
