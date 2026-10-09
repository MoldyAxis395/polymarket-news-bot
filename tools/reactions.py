"""Did the market move BEFORE or AFTER the bot saw the news? Usage: python tools/reactions.py [days]

For every signal in data/signals.jsonl (traded or skipped) it reads the outcome's price history:
  before = price when the bot saw it - price when the news was published  (already priced in)
  +5/+15/+60 = price N min after the bot saw it - price when the bot saw it  (edge left for us)
Values in cents, positive = in the bot's favour. Grouped by source type.
"""
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import config, polymarket as pm  # noqa: E402

FAST = {"espn_injuries": "fast:espn", "mlb_lineups": "fast:mlb", "soccer_lineups": "fast:soccer", "bluesky": "fast:bluesky"}
CACHE = config.DATA / "reactions_cache.json"
days = float(sys.argv[1]) if len(sys.argv) > 1 else 7

cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
rows = [json.loads(line) for line in (config.DATA / "signals.jsonl").open(encoding="utf-8")]
rows = [r for r in rows if r.get("token") and r["ts"] > time.time() - days * 86400]
done = [r for r in rows if r["ts"] < time.time() - 3700]
print(f"{len(rows)} signals with price data in last {days:g} days, {len(done)} older than 1h")

groups = defaultdict(list)
for r in done:
    key = f"{r['token']}:{r['ts']}"
    if key not in cache:
        try:
            hist = pm.price_history(r["token"], r["published"] - 1800, r["ts"] + 3900)
        except Exception as e:
            print(f"  history error: {e}")
            continue
        p0 = pm.price_at(r["token"], r["published"], hist)
        ps = pm.price_at(r["token"], r["ts"], hist)
        after = {m: pm.price_at(r["token"], r["ts"] + m * 60, hist) for m in (5, 15, 60)}
        cache[key] = None if ps is None else {
            "before": None if p0 is None else ps - p0, **{f"+{m}": None if p is None else p - ps for m, p in after.items()}}
        time.sleep(0.1)
    if cache[key] is None:
        continue
    src = FAST.get(r["src"], "rss")
    kind = "BUY" if r["action"] == "BUY" else "skip"
    groups[(src, kind)].append(cache[key])
    groups[(src, "all")].append(cache[key])
CACHE.write_text(json.dumps(cache))


def fmt(vals):
    vals = [v * 100 for v in vals if v is not None]
    if not vals:
        return "      -      "
    return f"{mean(vals):+5.1f}/{median(vals):+5.1f}"


print(f"\n{'source':14} {'set':5} {'n':>4}  {'before':>13} {'+5min':>13} {'+15min':>13} {'+60min':>13}   (cents, mean/median)")
for (src, kind), vs in sorted(groups.items()):
    print(f"{src:14} {kind:5} {len(vs):4}  " + " ".join(f"{fmt([v[k] for v in vs]):>13}" for k in ("before", "+5", "+15", "+60")))
print("\nbefore >> +15/+60: news already priced in when the bot saw it. +15/+60 > ~3c: edge left after spread.")
