"""Portfolio backtest of the crew's strategy on daily bars.
Signal on bar t's close -> fill at bar t+1's open. Exits: stop, 3R target,
close below SMA20, or 30-bar time stop. 0.05% slippage+fees each side."""
import pandas as pd

import config as C
from crew import indicators, scout_signal, quant_size, builder_check

COST = 0.0005


def run(raw, warmup=200):
    data = {s: indicators(df) for s, df in raw.items()}
    dates = sorted(set().union(*[d.index for d in data.values()]))
    cash, positions, trades, curve = C.STARTING_CAPITAL, {}, [], []
    pending = []  # signals waiting for next open

    for i, day in enumerate(dates):
        # 1) fill yesterday's signals at today's open
        equity = cash + sum(p["qty"] * p["last"] for p in positions.values())
        for sig in pending:
            d = data[sig["symbol"]]
            if day not in d.index:
                continue
            o = float(d.loc[day, "open"]) * (1 + COST)
            size = quant_size(equity, cash, o, sig["atr"], sig["symbol"])
            if not size:
                continue
            order = {"symbol": sig["symbol"], **size}
            ok, _ = builder_check(order, equity, cash, positions)
            if ok:
                cash -= size["cost"]
                positions[sig["symbol"]] = {**size, "entry": o, "date": day, "bars": 0, "last": o}
        pending = []

        # 2) manage open positions on today's bar
        for sym in list(positions):
            d = data[sym]
            if day not in d.index:
                continue
            bar, p = d.loc[day], positions[sym]
            p["bars"] += 1
            exit_px, why = None, None
            if bar.low <= p["stop"]:
                exit_px, why = min(bar.open, p["stop"]), "stop"
            elif bar.high >= p["target"]:
                exit_px, why = max(bar.open, p["target"]), "target"
            elif bar.close < bar.sma20:
                exit_px, why = bar.close, "trend"
            elif p["bars"] >= C.MAX_HOLD_BARS:
                exit_px, why = bar.close, "time"
            if exit_px is not None:
                px = float(exit_px) * (1 - COST)
                cash += p["qty"] * px
                pnl = p["qty"] * (px - p["entry"])
                trades.append({"symbol": sym, "entry_date": p["date"].date(), "exit_date": day.date(),
                               "entry": p["entry"], "exit": px, "qty": p["qty"], "pnl": pnl,
                               "R": pnl / p["risk_usd"] if p["risk_usd"] else 0, "why": why})
                del positions[sym]
            else:
                p["last"] = float(bar.close)

        # 3) new signals (after warmup)
        if i >= warmup:
            cands = []
            for sym, d in data.items():
                if day in d.index and sym not in positions and scout_signal(d.loc[day]):
                    r = d.loc[day]
                    cands.append({"symbol": sym, "atr": float(r.atr),
                                  "score": r.mom63 / r.vol63 if r.vol63 else 0})
            pending = sorted(cands, key=lambda c: c["score"], reverse=True)

        equity = cash + sum(p["qty"] * p["last"] for p in positions.values())
        curve.append((day, equity))

    curve = pd.Series(dict(curve))
    return trades, curve


def stats(trades, curve):
    t = pd.DataFrame(trades)
    dd = (curve / curve.cummax() - 1).min()
    years = (curve.index[-1] - curve.index[0]).days / 365.25
    out = {
        "start": curve.index[0].date(), "end": curve.index[-1].date(),
        "final_equity": round(curve.iloc[-1], 2),
        "total_return_pct": round((curve.iloc[-1] / C.STARTING_CAPITAL - 1) * 100, 2),
        "cagr_pct": round(((curve.iloc[-1] / C.STARTING_CAPITAL) ** (1 / years) - 1) * 100, 2) if years > 0 else 0,
        "max_drawdown_pct": round(dd * 100, 2),
        "trades": len(t),
    }
    if len(t):
        out.update({
            "win_rate_pct": round((t.pnl > 0).mean() * 100, 1),
            "avg_R": round(t.R.mean(), 2),
            "worst_trade_R": round(t.R.min(), 2),
            "profit_factor": round(t.pnl[t.pnl > 0].sum() / -t.pnl[t.pnl < 0].sum(), 2) if (t.pnl < 0).any() else None,
        })
    return out


if __name__ == "__main__":
    from data import bars_many
    raw = bars_many(C.all_symbols(), "5y")
    trades, curve = run(raw)
    for k, v in stats(trades, curve).items():
        print(f"{k:>18}: {v}")
    pd.DataFrame(trades).to_csv("backtest_trades.csv", index=False)
