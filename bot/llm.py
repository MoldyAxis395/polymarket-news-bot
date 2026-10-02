"""LLM brain: free OpenAI-compatible chat API (Pollinations by default, no key needed).

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

    def __init__(self, fallback):
        self.fallback = fallback
        self.key = os.environ.get(config.LLM_API_KEY_ENV, "")
        self.last_call = 0.0
        self.usage = {}
        if USAGE.exists():
            try:
                self.usage = json.loads(USAGE.read_text())
            except Exception:
                pass

    # ---------- plumbing ----------
    def _today(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def calls_today(self):
        return self.usage.get(self._today(), 0)

    def _ask(self, prompt):
        """Returns parsed JSON dict, or None on any failure / budget exhausted."""
        if self.calls_today() >= config.LLM_DAILY_CAP:
            return None
        wait = config.LLM_MIN_INTERVAL - (time.time() - self.last_call)
        if wait > 0:
            time.sleep(wait)
        self.last_call = time.time()
        day = self._today()
        self.usage = {day: self.usage.get(day, 0) + 1}
        USAGE.write_text(json.dumps(self.usage))
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        try:
            r = requests.post(config.LLM_URL, timeout=180, headers=headers, json={
                "model": config.LLM_MODEL, "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            txt = r.json()["choices"][0]["message"]["content"]
            return json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
        except Exception:
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
            'Answer JSON only: {"market": i, "outcome": j, "confidence": 0.0-1.0, "reason": "short"} '
            'where outcome j should RISE, or {"market": null, "reason": "short"}.')
        if d is None:  # API down: rule brain with its own strict matching
            strict = index.candidates(item.title) if index is not None else []
            return self.fallback.analyze(item, strict)
        if d.get("market") is None:
            return []
        try:
            m = candidates[int(d["market"])][0]
            j = int(d["outcome"])
            if j not in (0, 1):
                return []
            return [Signal(m, j, float(d.get("confidence", 0)), "llm: " + str(d.get("reason", ""))[:200])]
        except (ValueError, IndexError, KeyError, TypeError):
            return []
