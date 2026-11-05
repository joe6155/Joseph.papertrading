"""The crew.

Scout   - scans the universe and flags setups (trend + breakout).
Quant   - scores setups and sizes them to the 1% risk rule.
Builder - turns sized setups into orders and enforces every desk rule.
"""
import math

import numpy as np
import pandas as pd

import config as C


# ---------- indicators ----------
def indicators(df):
    d = df.copy()
    d["sma20"] = d.close.rolling(20).mean()
    d["sma50"] = d.close.rolling(50).mean()
    d["sma200"] = d.close.rolling(200).mean()
    prev = d.close.shift()
    tr = pd.concat([d.high - d.low, (d.high - prev).abs(), (d.low - prev).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()
    d["hi20"] = d.high.rolling(20).max().shift()          # prior 20-bar high
    delta = d.close.diff()
    up = delta.clip(lower=0).rolling(14).mean()
    dn = (-delta.clip(upper=0)).rolling(14).mean()
    d["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    d["mom63"] = d.close / d.close.shift(63) - 1
    d["vol63"] = d.close.pct_change().rolling(63).std() * math.sqrt(252)
    return d


# ---------- Scout ----------
def scout_signal(row):
    """Long-only swing setup: uptrend + fresh 20-day breakout, not overextended."""
    if any(pd.isna(row[k]) for k in ("sma50", "sma200", "hi20", "atr", "rsi")):
        return False
    return (row.close > row.sma50 > row.sma200 and
            row.close > row.hi20 and
            row.rsi < 75)


def scout(data):
    """data: {symbol: indicator df}. Returns setups on the latest bar."""
    hits = []
    for sym, d in data.items():
        row = d.iloc[-1]
        if scout_signal(row):
            hits.append({"symbol": sym, "date": d.index[-1].date(), "close": float(row.close),
                         "atr": float(row.atr), "rsi": float(row.rsi),
                         "mom63": float(row.mom63), "vol63": float(row.vol63)})
    return hits


# ---------- Quant ----------
def quant_size(equity, cash, entry, atr, symbol):
    """Shares so that (entry - stop) * shares <= 1% of equity, capped by
    position limit and available cash (no leverage)."""
    stop = entry - C.ATR_STOP_MULT * atr
    risk_per_unit = entry - stop
    if risk_per_unit <= 0:
        return None
    qty = (equity * C.MAX_RISK_PER_TRADE) / risk_per_unit
    qty = min(qty, equity * C.MAX_POSITION_PCT / entry, cash / entry)
    fractional = C.asset_class(symbol) == "crypto_spot"
    qty = round(qty, 6) if fractional else math.floor(qty)
    if qty <= 0:
        return None
    return {"qty": qty, "stop": stop, "target": entry + C.TARGET_R * risk_per_unit,
            "risk_usd": qty * risk_per_unit, "cost": qty * entry}


def quant(setups, equity, cash):
    """Rank by risk-adjusted momentum, size each."""
    for s in setups:
        s["score"] = s["mom63"] / s["vol63"] if s["vol63"] else 0
    ranked = sorted(setups, key=lambda s: s["score"], reverse=True)
    out = []
    for s in ranked:
        size = quant_size(equity, cash, s["close"], s["atr"], s["symbol"])
        if size:
            out.append({**s, **size})
    return out


# ---------- Builder ----------
def builder_check(order, equity, cash, open_positions):
    """Return (ok, reason). Every desk rule lives here."""
    sym = order["symbol"].upper()
    if sym in C.BLOCKED or any(w in sym.lower() for w in C.LEVERAGE_WORDS):
        return False, "leveraged/inverse product"
    if C.asset_class(sym) == "unknown":
        return False, "not in approved universe"
    if order.get("side", "buy") != "buy" and not C.ALLOW_SHORT:
        return False, "shorting not allowed"
    if order.get("type") == "option":
        return False, "options not allowed"
    if sym in open_positions:
        return False, "already holding"
    if len(open_positions) >= C.MAX_OPEN_POSITIONS:
        return False, "max positions reached"
    if order["cost"] > cash + 1e-6:
        return False, "would need leverage"
    if order["risk_usd"] > equity * C.MAX_RISK_PER_TRADE + 1e-6:
        return False, "risk above 1%"
    if order["cost"] > equity * C.MAX_POSITION_PCT + 1e-6:
        return False, "position above 20% cap"
    return True, "ok"


def builder(sized, equity, cash, open_positions):
    orders, rejected = [], []
    held = set(open_positions)
    for o in sized:
        ok, why = builder_check({**o, "side": "buy"}, equity, cash, held)
        if ok:
            orders.append(o)
            cash -= o["cost"]
            held.add(o["symbol"])
        else:
            rejected.append((o["symbol"], why))
    return orders, rejected
