"""Paper bot: one run per morning. Updates the ledger with the latest bars,
closes positions that hit an exit, then lets the crew open new ones.
Simulated fills only — nothing touches a real broker."""
import datetime as dt
import json
import os

import config as C
from crew import indicators, scout, quant, builder
from data import bars_many

LEDGER = os.environ.get("DESK_LEDGER", "ledger.json")


def load():
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            return json.load(f)
    return {"cash": C.STARTING_CAPITAL, "positions": {}, "closed": [], "history": []}


def save(led):
    with open(LEDGER, "w") as f:
        json.dump(led, f, indent=2, default=str)


def run():
    led = load()
    data = {s: indicators(df) for s, df in bars_many(C.all_symbols(), "2y").items()}
    today = dt.date.today().isoformat()
    exits = []

    # manage open positions
    for sym, p in list(led["positions"].items()):
        d = data.get(sym)
        if d is None:
            continue
        bar = d.iloc[-1]
        p["bars"] = p.get("bars", 0) + (1 if str(d.index[-1].date()) != p.get("last_date") else 0)
        p["last_date"], p["last"] = str(d.index[-1].date()), float(bar.close)
        why = ("stop" if bar.low <= p["stop"] else
               "target" if bar.high >= p["target"] else
               "trend" if bar.close < bar.sma20 else
               "time" if p["bars"] >= C.MAX_HOLD_BARS else None)
        if why:
            px = p["stop"] if why == "stop" else p["target"] if why == "target" else float(bar.close)
            pnl = p["qty"] * (px - p["entry"])
            led["cash"] += p["qty"] * px
            rec = {"symbol": sym, **p, "exit": px, "exit_date": today, "pnl": pnl, "why": why}
            led["closed"].append(rec)
            exits.append(rec)
            del led["positions"][sym]

    equity = led["cash"] + sum(p["qty"] * p["last"] for p in led["positions"].values())

    # crew
    setups = scout(data)
    sized = quant(setups, equity, led["cash"])
    orders, rejected = builder(sized, equity, led["cash"], led["positions"])
    for o in orders:
        led["cash"] -= o["cost"]
        led["positions"][o["symbol"]] = {
            "qty": o["qty"], "entry": o["close"], "stop": o["stop"], "target": o["target"],
            "risk_usd": o["risk_usd"], "entry_date": today, "bars": 0,
            "last": o["close"], "last_date": str(o["date"])}

    equity = led["cash"] + sum(p["qty"] * p["last"] for p in led["positions"].values())
    led["history"].append({"date": today, "equity": round(equity, 2), "cash": round(led["cash"], 2)})
    save(led)
    return led, setups, orders, rejected, exits, equity, data


def brief(led, setups, orders, rejected, exits, equity, data):
    L = [f"# Desk brief — {dt.date.today():%a %b %d, %Y}", "",
         f"**Paper equity:** ${equity:,.2f}  ·  cash ${led['cash']:,.2f}  ·  "
         f"{(equity / C.STARTING_CAPITAL - 1) * 100:+.2f}% since start", ""]
    L.append("## Market tape")
    for s in ["SPY", "QQQ", "BTC-USD", "GLD", "TLT", "UUP"]:
        d = data.get(s)
        if d is not None and len(d) > 1:
            c, p = d.close.iloc[-1], d.close.iloc[-2]
            trend = "above" if c > d.sma50.iloc[-1] else "below"
            L.append(f"- {s}: {c:,.2f} ({(c / p - 1) * 100:+.2f}%), {trend} 50-day")
    L += ["", "## Exits"] + ([f"- {e['symbol']}: {e['why']} at {e['exit']:,.2f}, P&L ${e['pnl']:+,.2f}" for e in exits] or ["- none"])
    L += ["", "## New paper entries"] + ([f"- BUY {o['qty']} {o['symbol']} @ {o['close']:,.2f} · stop {o['stop']:,.2f} · "
                                          f"target {o['target']:,.2f} · risk ${o['risk_usd']:,.2f}" for o in orders] or ["- none"])
    L += ["", "## Open positions"]
    for s, p in led["positions"].items():
        L.append(f"- {s}: {p['qty']} @ {p['entry']:,.2f} → {p['last']:,.2f} "
                 f"(${p['qty'] * (p['last'] - p['entry']):+,.2f}), stop {p['stop']:,.2f}")
    if not led["positions"]:
        L.append("- none")
    L += ["", "## Scout watchlist"] + ([f"- {s['symbol']}: breakout, RSI {s['rsi']:.0f}, 3-mo mom {s['mom63'] * 100:+.1f}%" for s in setups] or ["- no setups"])
    if rejected:
        L += ["", "## Builder rejections"] + [f"- {s}: {why}" for s, why in rejected]
    L += ["", "_Paper trading only. Rules: long-only, no options, no leverage, 1% max risk, swing holds._"]
    return "\n".join(L)


if __name__ == "__main__":
    out = brief(*run())
    os.makedirs("briefs", exist_ok=True)
    with open(f"briefs/brief_{dt.date.today()}.md", "w") as f:
        f.write(out)
    print(out)
