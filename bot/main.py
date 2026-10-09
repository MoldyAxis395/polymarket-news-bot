"""News -> Polymarket paper bot. Run: python -m bot.main"""
import json
import re
import sys
import time
import traceback
from datetime import datetime

from . import config, polymarket as pm
from .brain import make_brain
from .matcher import MarketIndex
from .fastfeeds import FastFeeds
from .news import NewsFeed
from .paper import Paper

LOG = config.DATA / "bot.log"
SIGNALS = config.DATA / "signals.jsonl"
STATUS = config.DATA / "status.json"


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def record_signal(item, sig, action):
    with SIGNALS.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": datetime.now().isoformat(timespec="seconds"), "news": item.title,
                            "src": item.source, "pub": item.publisher, "age_min": round(item.age_min, 1),
                            "market": sig.market.question, "buy": sig.market.outcomes[sig.outcome],
                            "price": sig.market.prices[sig.outcome], "conf": sig.confidence,
                            "reason": sig.reason, "action": action,
                            # for tools/reactions.py: price path around the news
                            "token": sig.market.token_ids[sig.outcome], "ts": round(time.time()),
                            "published": round(item.published.timestamp())}) + "\n")


def entry_check(sig, idx_prices, item):
    """Live checks before buying. Returns None if ok, else reason to skip."""
    m = sig.market
    if sig.confidence < config.MIN_CONFIDENCE:
        return f"low conf {sig.confidence}"
    if m.liquidity < config.MIN_LIQUIDITY or m.volume24h < config.MIN_VOLUME_24H:
        return "illiquid"
    if m.minutes_to_end() < config.MIN_MINUTES_TO_END:
        return "closing soon"
    bid, ask = pm.best_bid_ask(m.token_ids[sig.outcome])
    if bid is None or ask is None:
        return "empty book"
    if ask - bid > config.MAX_SPREAD:
        return f"spread {ask - bid:.3f}"
    if not (config.ENTRY_PRICE_MIN <= ask <= config.ENTRY_PRICE_MAX):
        return f"ask {ask} out of range"
    # moved since the news was PUBLISHED (index prices can be 10 min old: missed most moves)
    token = m.token_ids[sig.outcome]
    try:
        ref = pm.price_at(token, item.published.timestamp())
    except Exception:
        ref = None
    ref = ref if ref is not None else idx_prices.get(token)
    mid = (bid + ask) / 2
    if ref is not None and mid - ref > config.ALREADY_MOVED:
        return f"already moved {ref:.3f}->{mid:.3f}"
    return None


def write_status(paper, brain, n_markets, n_news):
    STATUS.write_text(json.dumps({
        "updated": datetime.now().isoformat(timespec="seconds"), "mode": config.MODE,
        "brain": brain.name, "llm_calls_today": getattr(brain, "calls_today", lambda: 0)(), "markets_indexed": n_markets, "news_processed": n_news,
        "cash": round(paper.cash, 2), "equity": round(paper.equity(), 2),
        "start": config.START_CASH, "open": paper.positions,
        "closed_count": len(paper.closed),
        "llm": brain.stats() if hasattr(brain, "stats") else None,
        "realized_pnl": round(sum(c["pnl"] for c in paper.closed), 2)}, indent=1))


def run():
    import os
    (config.DATA / "bot.pid").write_text(str(os.getpid()))
    if sys.platform == "win32":  # keep PC awake while bot runs (released on exit)
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    log(f"=== start mode={config.MODE} pid={os.getpid()} ===")
    brain = make_brain(log)
    paper = Paper(log)
    feed = NewsFeed(log)
    fast = FastFeeds(log)
    index, idx_prices, last_refresh = None, {}, 0.0
    last_news = last_pos = 0.0
    n_news = 0
    log(f"brain={brain.name} cash={paper.cash:.2f} open={len(paper.positions)}")
    if feed.seen:
        log(f"resuming with {len(feed.seen)} known news items")
    else:
        log(f"priming news: {feed.prime()} existing items ignored")
    # RUN_SECONDS: stop cleanly after N seconds (GitHub Actions shifts are max 6h)
    stop_at = time.time() + float(os.environ.get("RUN_SECONDS", "inf"))

    while time.time() < stop_at:
        try:
            now = time.time()
            if now - last_refresh > config.MARKET_REFRESH_SEC or index is None:
                markets = pm.fetch_markets()
                skip_q = re.compile(config.SKIP_QUESTION, re.I)
                markets = [m for m in markets if m.liquidity >= config.MIN_LIQUIDITY
                           and m.minutes_to_end() <= config.MAX_DAYS_TO_END * 1440
                           and not skip_q.search(m.question)]
                index = MarketIndex(markets)
                idx_prices = {t: p for m in markets for t, p in zip(m.token_ids, m.prices)}
                last_refresh = now
                log(f"indexed {len(markets)} markets")

            items = fast.poll()  # each fast source keeps its own interval
            if now - last_news > config.NEWS_POLL_SEC:
                last_news = now
                items += feed.poll()
            if items:
                for item in items:
                    n_news += 1
                    if brain.name == "llm":
                        if not brain.worth_asking(item):
                            continue
                        cands = index.candidates_loose(item.title)
                    else:
                        cands = index.candidates(item.title)
                        if not cands:
                            continue
                    sigs = brain.analyze(item, cands, index)
                    if not sigs:
                        continue
                    log(f"NEWS [{item.publisher or item.source} {item.age_min:.0f}m] {item.title}")
                    for sig in sigs:
                        why = None
                        if brain.name == "llm" and sig.reason.startswith("rules") and not config.RULES_CAN_TRADE:
                            why = "rules fallback, LLM down (not traded)"
                        if not sig.trade:
                            why = "rss sport (measure only)"
                        why = why or paper.can_open(sig.market.id, sig.market.outcomes[sig.outcome]) or entry_check(sig, idx_prices, item)
                        if why:
                            log(f"  skip {sig.market.outcomes[sig.outcome]} :: {sig.market.question[:70]} ({why})")
                            record_signal(item, sig, "skip: " + why)
                            continue
                        record_signal(item, sig, "BUY")
                        paper.open(sig, item.title)
                        break  # one trade per headline

            if now - last_pos > config.POSITION_CHECK_SEC:
                last_pos = now
                paper.check_exits()
                write_status(paper, brain, len(index.markets), n_news)
        except KeyboardInterrupt:
            raise
        except Exception:
            log("ERROR " + traceback.format_exc().strip().replace("\n", " | ")[-600:])
            time.sleep(10)
        time.sleep(2)
    paper.save()
    log("shift over, state saved")


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        log("stopped")
        sys.exit(0)
