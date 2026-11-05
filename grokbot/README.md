# Grok Bot: Solana memecoin paper desk

**Paper trading only.** No wallet, no private keys, no swaps. Every fill is simulated against public market data. Python 3.9+, standard library only.

## Run it

```bash
python3 -m grokbot.desk --check      # 1. confirm the APIs answer and the fields parse
python3 -m grokbot.desk --once       # 2. one full cycle
./grokbot/run_overnight.sh 10        # 3. 10 hours in the background (keeps a Mac awake)
python3 -m grokbot.report            # any time: equity, exits, open positions, kill tallies
python3 -m unittest grokbot.tests.test_desk   # offline tests
```

Each run writes to `grokbot/state/`: `ledger.json` (cash and positions), `kills.jsonl` (every rejected coin and why), `events.jsonl` (entries, exits, errors) and `run.log`. When a run ends, a report goes to `grokbot/reports/`. Add `--mode record` to save every API response to `grokbot/recordings/`, then use `--mode replay` to re-run against those saved responses offline.

## The six agents (hard handoffs)

Each agent only accepts the previous agent's output type, so nothing can skip a stage. The Executor refuses any order that the Risk agent hasn't armed with a stop.

| Agent | Job | Data |
|---|---|---|
| **Scout** | Finds fresh Solana coins: pair age 30m–72h, market cap $300k–$20M | DexScreener profiles/boosts + GeckoTerminal new/trending pools → DexScreener pair data |
| **Filter** | Kills a coin if: mint/freeze authority not revoked, LP <95% burned/locked, liquidity <$40k, 1h volume <$50k, top 10 holders >25%, dev >3% or flagged rug history, bundled/insider share >10%, already +80% in 1h. A check with **missing data also kills.** | RugCheck report |
| **Analyst** | Scores 0–100 and passes only 70+. Weights: momentum 20, unique buyers 20, buy/sell 15, holder growth 15, liq/mcap 15, smart wallets 15 | DexScreener + RugCheck |
| **Executor** | Sizes the trade at up to 8% of equity. Fill = quote + 1% slippage + size/liquidity impact + 0.25% fee | — |
| **Risk** | Sets the −6% stop *before* the fill. Once the peak reaches +12%, trails 6% below the peak. Checks 1-minute candles every 60s | GeckoTerminal OHLCV (DexScreener quote as fallback) |
| **Logger** | Records every kill (every reason, not just the first), entry, exit and error | `state/*.jsonl` |

## Where the data is approximate

- **Unique buyers:** DexScreener only gives 1h buy-transaction *counts*. Those are used as a proxy, and bots can inflate them.
- **Holder growth:** RugCheck's holder count, snapshotted between scans. The first time a coin is seen there's no growth figure, so that component is dropped and the weights are renormalised.
- **Smart wallets:** turned off until you put wallet addresses, one per line, in `grokbot/smart_wallets.txt`. Matches are only checked against RugCheck's top-holder list.
- **Bundled launch:** RugCheck's insider flags and insider networks. This is a proxy, not true same-block bundle detection.
- **Dev rug history:** RugCheck risk flags that mention creator/dev rugs.
- **Trail distance:** 6% is my default because you didn't specify one. It's `TRAIL_PCT` in `config.py`.
- No cap on how many positions can be open at once (only the 8% per trade and available cash limit it). The same coin can't be bought twice or re-bought within 6h of exiting.

Paper results will look better than live trading would. Real memecoin fills suffer from MEV, sniper bots and liquidity that vanishes on the way down, and a stop can gap well past −6%. The model fills gaps at the candle open, but a 1-minute candle can still hide a worse fill.

All thresholds are in `config.py`.
