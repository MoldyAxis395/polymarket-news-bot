"""Paper portfolio: fills simulated against the real live order book."""
import csv
import json
import time
from datetime import datetime, timedelta, timezone

from . import config, polymarket as pm
from .notify import telegram

STATE = config.DATA / "portfolio.json"
TRADES = config.DATA / "trades.csv"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Paper:
    """hold_to_resolution: no take profit / stop loss / max hold, keep until the market settles
    (copy-trading a pick on the final result). label prefixes Telegram messages."""

    def __init__(self, log, state=STATE, trades=TRADES, label="", start_cash=config.START_CASH,
                 per_trade=config.MAX_PER_TRADE, hold_to_resolution=False, news_label="Notizia"):
        self.log = log
        self.state_path, self.trades_path, self.label = state, trades, label
        self.start_cash, self.per_trade, self.hold = start_cash, per_trade, hold_to_resolution
        self.news_label = news_label
        if state.exists():
            s = json.loads(state.read_text())
        else:
            s = {"cash": start_cash, "positions": [], "closed": [], "cooldown": {}}
        self.cash = s["cash"]
        self.positions = s["positions"]
        self.closed = s["closed"]
        self.cooldown = s["cooldown"]

    def save(self):
        self.state_path.write_text(json.dumps({"cash": self.cash, "positions": self.positions,
                                     "closed": self.closed, "cooldown": self.cooldown}, indent=1))

    def _trade_row(self, row):
        new = not self.trades_path.exists()
        with self.trades_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "action", "market", "outcome", "shares", "price", "usd", "pnl", "reason"])
            w.writerow(row)

    def can_open(self, market_id, outcome=None):
        if config.MAX_OPEN_POSITIONS and len(self.positions) >= config.MAX_OPEN_POSITIONS:
            return "max positions"
        if self.cash < config.MIN_TRADE_USD:
            return "no cash"
        if any(p["market_id"] == market_id for p in self.positions):
            return "already in market"
        until = self.cooldown.get(market_id, 0)
        if time.time() < until:
            return "cooldown"
        cutoff = time.time() - config.NO_FLIP_HOURS * 3600
        if outcome and any(c["market_id"] == market_id and c["outcome"] != outcome and c["opened"] > cutoff
                           for c in self.closed):
            return "would flip side"
        return None

    def open(self, sig, news_title):
        m = sig.market
        token = m.token_ids[sig.outcome]
        usd = min(self.per_trade, self.cash)
        shares, avg = pm.simulate_buy(token, usd)
        if not shares:
            self.log(f"  no asks to buy on {m.question}")
            return None
        cost = shares * avg
        self.cash -= cost
        p = {"market_id": m.id, "question": m.question, "slug": m.slug, "outcome": m.outcomes[sig.outcome],
             "token": token, "shares": shares, "entry": avg, "cost": cost, "opened": time.time(),
             "opened_iso": _now(), "news": news_title, "reason": sig.reason, "conf": sig.confidence,
             "end": m.end.isoformat() if m.end else None, "last_bid": None}
        self.positions.append(p)
        self.save()
        self._trade_row([_now(), "BUY", m.question, p["outcome"], f"{shares:.2f}", f"{avg:.4f}",
                         f"{cost:.2f}", "", f"{news_title} | {sig.reason}"])
        self.log(f"  BUY {p['outcome']} @ {avg:.3f} x{shares:.1f} (${cost:.2f}) :: {m.question}")
        telegram("\n".join([
            f"{self.label}🟢 APERTO (paper)",
            m.question,
            f"Compro: {p['outcome']} @ {avg:.3f} x{shares:.1f} = ${cost:.2f}",
            f"{self.news_label}: {news_title}",
            f"Conf {sig.confidence} | cassa ${self.cash:.2f}",
            f"https://polymarket.com/market/{m.slug}"]))
        return p

    def check_exits(self):
        for p in list(self.positions):
            age_h = (time.time() - p["opened"]) / 3600
            try:
                bid, _ = pm.best_bid_ask(p["token"])
            except Exception as e:
                if not p.get("book_err"):  # log once, not every 20 s
                    self.log(f"  book error {p['question'][:50]}: {e}")
                    p["book_err"] = True
                self._try_settle(p)
                continue
            p["book_err"] = False
            p["last_bid"] = bid
            if bid is None:
                if age_h >= config.MAX_HOLD_HOURS or self.hold:
                    self._try_settle(p)
                continue
            why = None
            if self.hold:
                # pick on the final result: wait for settlement (book closes -> _try_settle)
                if p["end"] and datetime.now(timezone.utc) > datetime.fromisoformat(p["end"]) + timedelta(hours=3):
                    self._try_settle(p)
                continue
            if bid >= p["entry"] + config.TAKE_PROFIT:
                why = "take profit"
            elif bid <= p["entry"] - config.STOP_LOSS:
                why = "stop loss"
            elif age_h >= config.MAX_HOLD_HOURS:
                why = "max hold"
            elif p["end"]:
                end = datetime.fromisoformat(p["end"])
                if (end - datetime.now(timezone.utc)).total_seconds() < 5 * 60:
                    why = "market closing"
            if why:
                self.close(p, why)
        self.save()

    def _try_settle(self, p):
        """No order book (market closed/resolved): settle at Gamma's final outcome price."""
        if time.time() < p.get("settle_retry", 0):
            return
        p["settle_retry"] = time.time() + 600  # Gamma check at most every 10 min
        try:
            m = pm.get_market(p["market_id"])
            if not m.get("closed"):
                return
            outcomes = json.loads(m.get("outcomes") or "[]")
            prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
            if m.get("umaResolutionStatus") != "resolved" and not all(x in (0.0, 0.5, 1.0) for x in prices):
                return  # closed but not final yet
            price = prices[outcomes.index(p["outcome"])]
        except Exception as e:
            self.log(f"  settle error {p['question'][:50]}: {e}")
            return
        self._book_close(p, p["shares"] * price, price, p["shares"], "resolved")

    def close(self, p, why):
        usd, avg = pm.simulate_sell(p["token"], p["shares"])
        if avg is None:
            return
        self._book_close(p, usd, avg, usd / avg, why)

    def _book_close(self, p, usd, avg, sold, why):
        frac = min(1.0, sold / p["shares"])
        cost_part = p["cost"] * frac
        pnl = usd - cost_part
        self.cash += usd
        self._trade_row([_now(), "SELL", p["question"], p["outcome"], f"{sold:.2f}", f"{avg:.4f}",
                         f"{usd:.2f}", f"{pnl:+.2f}", why])
        self.log(f"  SELL {p['outcome']} @ {avg:.3f} ({why}) pnl {pnl:+.2f} :: {p['question']}")
        realized = sum(c["pnl"] for c in self.closed) + pnl
        hold_h = (time.time() - p["opened"]) / 3600
        pct = pnl / cost_part * 100 if cost_part else 0
        telegram("\n".join([
            f"{self.label}{'✅' if pnl >= 0 else '🔴'} CHIUSO (paper) — {why}",
            p["question"],
            f"{p['outcome']}: {p['entry']:.3f} → {avg:.3f} in {hold_h:.1f}h",
            f"P&L trade: {pnl:+.2f}$ ({pct:+.1f}%)",
            f"P&L totale realizzato: {realized:+.2f}$ | cassa ${self.cash:.2f}"]))
        if frac >= 0.999:
            self.positions.remove(p)
            self.closed.append({**p, "exit": avg, "pnl": pnl, "why": why, "closed_iso": _now()})
            self.cooldown[p["market_id"]] = time.time() + config.COOLDOWN_MIN_PER_MARKET * 60
        else:  # thin book: keep remainder
            p["shares"] -= sold
            p["cost"] -= cost_part

    def equity(self):
        return self.cash + sum(p["shares"] * (p["last_bid"] or p["entry"]) for p in self.positions)
