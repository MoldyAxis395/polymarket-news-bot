"""Public Polymarket data: Gamma (market list) + CLOB (order books). No auth needed."""
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from . import config

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
_s = requests.Session()
_s.headers.update(config.UA)


@dataclass
class Market:
    id: str
    question: str
    slug: str
    event_title: str
    outcomes: list
    prices: list
    token_ids: list
    volume24h: float
    liquidity: float
    end: datetime | None
    best_bid: float | None
    best_ask: float | None
    tags: list = field(default_factory=list)

    @property
    def is_binary_yes_no(self):
        return [o.lower() for o in self.outcomes] == ["yes", "no"]

    def minutes_to_end(self):
        if not self.end:
            return 1e9
        return (self.end - datetime.now(timezone.utc)).total_seconds() / 60


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_markets(n=config.MARKETS_TO_INDEX):
    out, offset, page = [], 0, 500
    while len(out) < n:
        r = _s.get(f"{GAMMA}/markets", params={
            "active": "true", "closed": "false", "limit": page, "offset": offset,
            "order": "volume24hr", "ascending": "false"}, timeout=30)
        if r.status_code == 422:  # past API's max offset
            break
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for m in batch:
            try:
                if not m.get("enableOrderBook") or not m.get("acceptingOrders", True):
                    continue
                outcomes = json.loads(m.get("outcomes") or "[]")
                prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
                tokens = json.loads(m.get("clobTokenIds") or "[]")
                if len(outcomes) != 2 or len(tokens) != 2 or len(prices) != 2:
                    continue
                ev = (m.get("events") or [{}])[0]
                out.append(Market(
                    id=str(m["id"]), question=m.get("question", ""), slug=m.get("slug", ""),
                    event_title=ev.get("title", "") or "", outcomes=outcomes, prices=prices,
                    token_ids=tokens, volume24h=float(m.get("volume24hr") or 0),
                    liquidity=float(m.get("liquidityNum") or 0), end=_parse_dt(m.get("endDate")),
                    best_bid=m.get("bestBid"), best_ask=m.get("bestAsk")))
            except Exception:
                continue
        offset += page
        time.sleep(0.3)
    return out[:n]


def get_market(market_id):
    r = _s.get(f"{GAMMA}/markets/{market_id}", timeout=20)
    r.raise_for_status()
    return r.json()


def book(token_id):
    """Returns (bids, asks) as lists of (price, size), best first."""
    r = _s.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=15)
    r.raise_for_status()
    b = r.json()
    bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])), reverse=True)
    asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks", []))
    return bids, asks


def simulate_buy(token_id, usd):
    """Walk the asks spending `usd`. Returns (shares, avg_price) or (0, None)."""
    _, asks = book(token_id)
    spent = shares = 0.0
    for price, size in asks:
        take = min(size, (usd - spent) / price)
        if take <= 0:
            break
        shares += take
        spent += take * price
        if spent >= usd - 1e-9:
            break
    return (shares, spent / shares) if shares else (0.0, None)


def simulate_sell(token_id, shares):
    """Walk the bids selling `shares`. Returns (usd_received, avg_price)."""
    bids, _ = book(token_id)
    left, got = shares, 0.0
    for price, size in bids:
        take = min(size, left)
        got += take * price
        left -= take
        if left <= 1e-9:
            break
    sold = shares - left
    return got, (got / sold if sold else None)


def best_bid_ask(token_id):
    bids, asks = book(token_id)
    return (bids[0][0] if bids else None), (asks[0][0] if asks else None)
