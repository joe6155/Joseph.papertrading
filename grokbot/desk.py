"""Grok Bot: Solana memecoin PAPER desk. No wallet, no keys, no real orders.

  python3 -m grokbot.desk --check            # hit each API once, show what came back
  python3 -m grokbot.desk --once             # one scout + risk cycle
  python3 -m grokbot.desk --hours 10         # run overnight, report at the end
  python3 -m grokbot.report                  # summary any time
"""
import argparse
import os
import time

from . import config as C
from .agents import Analyst, Executor, Filter, Ledger, Logger, Risk, Scout
from .sources import DataError, Http, Market

HERE = os.path.dirname(os.path.abspath(__file__))


def build(args):
    http = Http(args.mode, args.data)
    market = Market(http)
    log = Logger(args.state)
    ledger = Ledger(os.path.join(args.state, "ledger.json"))
    ex = Executor(ledger, log)
    return {
        "log": log, "ledger": ledger, "market": market,
        "scout": Scout(market, log), "filter": Filter(market, log),
        "analyst": Analyst(args.state, log), "executor": ex, "risk": Risk(market, ledger, ex, log),
    }


def scout_cycle(d):
    """Scout -> Filter -> Analyst -> Executor.ticket -> Risk.arm -> Executor.fill"""
    candidates = d["scout"].run()
    survivors = d["filter"].run(candidates)
    signals = d["analyst"].run(survivors)
    for sig in signals:
        t = d["executor"].ticket(sig)
        if t:
            d["executor"].fill(d["risk"].arm(t))


def check(args):
    m = Market(Http(args.mode, args.data))
    mints, errs = m.candidate_mints()
    print(f"candidate mints: {len(mints)}  feed errors: {len(errs)}")
    for e in errs:
        print("  !", e)
    if not mints:
        return
    pairs = m.pairs_for(mints[:30])
    print(f"DexScreener pairs: {len(pairs)}")
    p = next(iter(pairs.values()), None)
    if p:
        print("  sample:", {k: p[k] for k in ("symbol", "age_h", "mcap", "liq", "vol_h1", "chg_h1", "buys_h1", "sells_h1")})
        rep = m.rug_report(p["mint"])
        keys = ["mintAuthority", "freezeAuthority", "creator", "topHolders", "markets", "totalHolders",
                "insiderNetworks", "knownAccounts", "risks", "rugged", "token", "creatorBalance"]
        print("RugCheck fields present:", {k: (k in rep) for k in keys})
        if rep.get("topHolders"):
            print("  topHolders[0] keys:", sorted(rep["topHolders"][0].keys()))
        from .agents import parse_rugcheck
        s = parse_rugcheck(rep, p)
        print("  parsed:", {k: v for k, v in s.items() if k not in ("holder_owners", "risk_names")})
        bars = m.candles_1m(p["pair"], limit=5)
        print(f"GeckoTerminal 1m candles: {len(bars)}  last: {bars[-1] if bars else None}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=10)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--mode", choices=["live", "record", "replay"], default="live")
    ap.add_argument("--data", default=os.path.join(HERE, "recordings"))
    ap.add_argument("--state", default=os.path.join(HERE, "state"))
    args = ap.parse_args()
    if args.mode == "live":
        args.data = None
    if args.check:
        return check(args)

    d = build(args)
    log = d["log"]
    log.say(f"Grok Bot PAPER desk up. Equity ${d['ledger'].equity():,.2f}. Mode={args.mode}")
    end = time.time() + args.hours * 3600
    next_scout = 0
    try:
        while True:
            now = time.time()
            try:
                d["risk"].watch()
                if now >= next_scout:
                    scout_cycle(d)
                    next_scout = now + C.SCOUT_INTERVAL_S
            except DataError as e:
                log.event("cycle_error", error=str(e))
                next_scout = now + 60
            d["ledger"].save()
            if args.once or time.time() >= end:
                break
            time.sleep(max(1, C.RISK_INTERVAL_S - (time.time() - now)))
    except KeyboardInterrupt:
        log.say("stopped by you")
    finally:
        d["ledger"].save()
        from .report import write_report
        path = write_report(args.state)
        log.say(f"report: {path}")


if __name__ == "__main__":
    main()
