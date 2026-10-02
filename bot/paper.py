"""Paper portfolio: fills simulated against the real live order book."""
import csv
import json
import time
from datetime import datetime, timezone

from . import config, polymarket as pm
from .notify import telegram

STATE = config.DATA / "portfolio.json"
TRADES = config.DATA / "trades.csv"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Paper:
    def __init__(self, log):
        self.log = log
        if STATE.exists():
            s = json.loads(STATE.read_text())
        else:
            s = {"cash": config.START_CASH, "positions": [], "closed": [], "cooldown": {}}
        self.cash = s["cash"]
        self.positions = s["positions"]
        self.closed = s["closed"]
        self.cooldown = s["cooldown"]

    def save(self):
        STATE.write_text(json.dumps({"cash": self.cash, "positions": self.positions,
                                     "closed": self.closed, "cooldown": self.cooldown}, indent=1))

    def _trade_row(self, row):
        new = not TRADES.exists()
        with TRADES.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "action", "market", "outcome", "shares", "price", "usd", "pnl", "reason"])
            w.writerow(row)

    def can_open(self, market_id):
        if len(self.positions) >= config.MAX_OPEN_POSITIONS:
            return "max positions"
        if self.cash < 1:
            return "no cash"
        if any(p["market_id"] == market_id for p in self.positions):
            return "already in market"
        until = self.cooldown.get(market_id, 0)
        if time.time() < until:
            return "cooldown"
        return None

    def open(self, sig, news_title):
        m = sig.market
        token = m.token_ids[sig.outcome]
        usd = min(config.MAX_PER_TRADE, self.cash)
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
            "🟢 APERTO (paper)",
            m.question,
            f"Compro: {p['outcome']} @ {avg:.3f} x{shares:.1f} = ${cost:.2f}",
            f"Notizia: {news_title}",
            f"Conf {sig.confidence} | cassa ${self.cash:.2f}",
            f"https://polymarket.com/market/{m.slug}"]))
        return p

    def check_exits(self):
        for p in list(self.positions):
            try:
                bid, _ = pm.best_bid_ask(p["token"])
            except Exception as e:
                self.log(f"  book error {p['question'][:50]}: {e}")
                continue
            p["last_bid"] = bid
            if bid is None:
                continue
            age_h = (time.time() - p["opened"]) / 3600
            why = None
            if bid >= p["entry"] * (1 + config.TAKE_PROFIT):
                why = "take profit"
            elif bid <= p["entry"] * (1 - config.STOP_LOSS):
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

    def close(self, p, why):
        usd, avg = pm.simulate_sell(p["token"], p["shares"])
        if avg is None:
            return
        sold = usd / avg
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
            f"{'✅' if pnl >= 0 else '🔴'} CHIUSO (paper) — {why}",
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
