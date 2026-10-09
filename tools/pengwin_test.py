"""Check BOT PENGWIN without trading. Usage: python tools/pengwin_test.py
  1. past picks (league page 2) -> what the parser extracts
  2. each past pick mapped onto the markets of a current match (does the LLM read Italian bet terms?)
  3. Italian match names that need the LLM to find the Polymarket event
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import polymarket as pm  # noqa: E402
from bot.brain import make_brain  # noqa: E402
from bot.pengwin import BASE, MATCH_LINK, PICK, TITLE, Pengwin, _text  # noqa: E402

pw = Pengwin(make_brain(), print)
picks = []
for league in ["serie-a", "premier-league", "la-liga"]:
    for url in list(dict.fromkeys(MATCH_LINK.findall(pw._get(f"{BASE}{league}/pagina-2"))))[:5]:
        page = pw._get(url)
        m, t = PICK.search(page), TITLE.search(page)
        if m and "Non ancora" not in m.group(1):
            picks.append((_text(t.group(1)), _text(m.group(1))))
print(f"{len(picks)} past picks")

ev = pw.find_event("ARSENAL-LEEDS UNITED")
markets = [m for m in pm.event_markets(ev["slug"]) + pm.event_markets(ev["slug"] + "-more-markets")
           if m.liquidity >= 1000]
print(f"mapping onto {ev['title']} ({len(markets)} liquid markets)\n")
for match, pick in picks:
    d = pw.map_pick("ARSENAL-LEEDS UNITED", pick, markets) or {}
    tgt = (f"{markets[d['market']].question} -> {markets[d['market']].outcomes[d['outcome']]} [{d.get('match')}]"
           if d.get("market") is not None else "NONE")
    print(f"{match}: ...{pick[-90:]}\n    pick={d.get('pick')}  =>  {tgt}\n    {d.get('why')}", flush=True)

print()
for name in ["INTER-PARMA", "BARCELLONA-GETAFE", "LEVANTE-SIVIGLIA", "ALAVES-ATLETICO MADRID", "REAL MADRID-VILLARREAL"]:
    e = pw.find_event(name)
    print(f"{name} -> {e and e['title']}")
