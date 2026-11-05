# Joseph's paper trading desk

Paper trading only — no real money, no broker connection.

**Rules:** US stocks & ETFs, spot crypto, futures and forex through unleveraged ETFs only. $10k paper capital, 1% max risk per trade, swing holds (up to 30 days). Long-only: no options, no leverage, no shorting.

- `config.py` — desk rules and universe
- `data.py` — Yahoo Finance daily bars
- `crew.py` — Scout (finds setups), Quant (ranks and sizes), Builder (enforces rules)
- `backtest.py` — 5-year portfolio backtest
- `paperbot.py` — daily run: updates `ledger.json`, writes `briefs/brief_YYYY-MM-DD.md`

Daily run: `python3 paperbot.py` (then move the brief into `briefs/` and commit).
