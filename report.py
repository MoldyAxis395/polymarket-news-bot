"""Quick summary of the paper bot. Run: python report.py"""
import csv
import json
from collections import Counter
from pathlib import Path

D = Path(__file__).resolve().parent / "data"

st = json.loads((D / "status.json").read_text()) if (D / "status.json").exists() else None
if st:
    print(f"updated {st['updated']}  brain={st['brain']}  markets={st['markets_indexed']}  news seen={st['news_processed']}")
    print(f"cash ${st['cash']}  equity ${st['equity']}  (start ${st['start']})  "
          f"realized P&L ${st['realized_pnl']}  closed trades {st['closed_count']}")
    for p in st["open"]:
        print(f"  OPEN {p['outcome']} @{p['entry']:.3f} now bid {p['last_bid']}  :: {p['question'][:70]}")
        print(f"       news: {p['news'][:90]}")
else:
    print("no status yet")

if (D / "trades.csv").exists():
    print("\nTRADES")
    for r in csv.DictReader((D / "trades.csv").open(encoding="utf-8")):
        print(f"  {r['time'][11:19]} {r['action']:4} {r['outcome'][:14]:14} @{r['price']} ${r['usd']:>6} {r['pnl']:>6}  {r['market'][:55]}")

if (D / "signals.jsonl").exists():
    sig = [json.loads(l) for l in (D / "signals.jsonl").open(encoding="utf-8")]
    print(f"\nSIGNALS {len(sig)}  ->", dict(Counter(s['action'].split(':')[0] for s in sig)))
    for s in sig[-10:]:
        print(f"  {s['t'][11:19]} {s['action'][:28]:28} {s['buy'][:12]:12} :: {s['market'][:50]}  <= {s['news'][:50]}")
