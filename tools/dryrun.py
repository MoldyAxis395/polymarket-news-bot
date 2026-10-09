"""Run the configured brain over recent news without trading.
Usage: python tools/dryrun.py [minutes]   live news of the last N minutes
       python tools/dryrun.py replay      headlines of past closed trades: would the checks keep them?"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import config, polymarket as pm  # noqa: E402
from bot.brain import make_brain  # noqa: E402
from bot.matcher import MarketIndex  # noqa: E402
from bot.news import NewsFeed  # noqa: E402

if sys.argv[1:] == ["replay"]:
    import json
    from bot.llm import FLUFF_RE
    brain = make_brain()
    closed = json.loads((config.DATA / "portfolio.json").read_text(encoding="utf-8"))["closed"]
    kept = 0
    for c in closed:
        if c["reason"].startswith("rules"):
            continue
        tag = f"{c['pnl']:+.2f} {c['outcome']} :: {c['news'][:90]}"
        if FLUFF_RE.search(c["news"]):
            print(f"FLUFF   {tag}")
            continue
        f = brain._fact(type("I", (), {"title": c["news"], "summary": "", "publisher": "", "source": "replay"})())
        ok = (f and f.get("kind") in {"ruled_out", "suspended", "returns", "result", "resignation", "official_decision"}
              and f.get("confirmed") and f.get("new") and (f.get("key_player") or not f.get("person")))
        kept += bool(ok)
        print(f"{'KEEP' if ok else 'DROP'}    {tag}
        {json.dumps(f, ensure_ascii=False)[:300]}", flush=True)
    print(f"kept {kept}/{len(closed)} (then: 2nd source + market + side + verify checks)")
    sys.exit(0)

minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 180
skip_q = re.compile(config.SKIP_QUESTION, re.I)
markets = [m for m in pm.fetch_markets() if m.liquidity >= config.MIN_LIQUIDITY and not skip_q.search(m.question)]
index = MarketIndex(markets)
brain = make_brain()
items = [i for i in NewsFeed(print)._collect() if i.age_min <= minutes]
print(f"brain={brain.name} markets={len(markets)} news={len(items)}", flush=True)
t0, n_sig, n_asked = time.time(), 0, 0
for it in items:
    if brain.name == "llm":
        if not brain.worth_asking(it):
            continue
        cands = index.candidates_loose(it.title)
    else:
        cands = index.candidates(it.title)
        if not cands:
            continue
    n_asked += 1
    for s in brain.analyze(it, cands, index):
        n_sig += 1
        print(f"[{s.confidence:.2f}] {it.title[:100]}\n    -> BUY {s.market.outcomes[s.outcome]} "
              f"@{s.market.prices[s.outcome]:.2f} :: {s.market.question[:80]}\n    {s.reason[:160]}", flush=True)
calls = getattr(brain, "calls_today", lambda: 0)()
print(f"asked={n_asked} signals={n_sig} llm_calls={calls} time={time.time() - t0:.0f}s")
