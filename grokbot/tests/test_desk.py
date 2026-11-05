"""Offline tests with synthetic data: python3 -m unittest discover grokbot/tests"""
import tempfile
import time
import unittest

from grokbot import config as C
from grokbot.agents import (Analyst, ArmedOrder, Candidate, Executor, Filter, Ledger, Logger, Risk,
                            Scout, Signal, Ticket, parse_rugcheck)
from grokbot.desk import scout_cycle

NOW_MS = time.time() * 1000


def pair(**kw):
    p = {"mint": "MINTgood", "symbol": "GOOD", "name": "", "pair": "POOLgood", "dex": "raydium", "url": "",
         "price": 0.001, "liq": 150_000, "mcap": 1_500_000, "vol_h1": 400_000, "chg_m5": 2, "chg_h1": 25,
         "chg_h6": 60, "buys_h1": 1200, "sells_h1": 700, "age_h": 6}
    p.update(kw)
    return p


def rug(**kw):
    r = {"mintAuthority": None, "freezeAuthority": None, "creator": "DEV",
         "token": {"supply": 1_000_000_000},
         "markets": [{"lp": {"lpLockedPct": 100}}],
         "knownAccounts": {"AMMacct": {"name": "Raydium", "type": "AMM"}},
         "topHolders": [{"address": "AMMacct", "owner": "AMMowner", "pct": 30, "insider": False}] +
                       [{"address": f"H{i}", "owner": f"O{i}", "pct": 1.5, "insider": False} for i in range(12)],
         "totalHolders": 2000, "risks": [], "rugged": False}
    r.update(kw)
    return r


class FilterTests(unittest.TestCase):
    def test_clean_coin_passes(self):
        s = parse_rugcheck(rug(), pair())
        self.assertEqual(s["top10_pct"], 15.0)  # AMM account excluded
        self.assertEqual(Filter.checks(pair(), s), [])

    def test_each_kill(self):
        cases = [
            (pair(), rug(mintAuthority="X"), "mint authority"),
            (pair(), rug(freezeAuthority="X"), "freeze authority"),
            (pair(), rug(markets=[{"lp": {"lpLockedPct": 40}}]), "LP only"),
            (pair(liq=30_000), rug(), "liq"),
            (pair(vol_h1=20_000), rug(), "1h vol"),
            (pair(), rug(topHolders=[{"address": f"H{i}", "owner": f"O{i}", "pct": 4, "insider": False} for i in range(10)]), "top 10"),
            (pair(), rug(topHolders=[{"address": "D", "owner": "DEV", "pct": 5, "insider": False}]), "dev holds"),
            (pair(), rug(risks=[{"name": "Creator history of rugged tokens", "description": ""}]), "rug history"),
            (pair(), rug(topHolders=[{"address": f"H{i}", "owner": f"O{i}", "pct": 2.2, "insider": True} for i in range(6)]), "bundled"),
            (pair(chg_h1=95), rug(), "already +95%"),
            (pair(), {}, "no data"),
        ]
        for p, r, want in cases:
            reasons = Filter.checks(p, parse_rugcheck(r, p))
            self.assertTrue(any(want in x for x in reasons), (want, reasons))


class RiskTests(unittest.TestCase):
    def pos(self):
        return {"entry": 1.0, "stop": 0.94, "peak": 1.0, "trailing": False}

    def test_hard_stop(self):
        p = self.pos()
        self.assertIsNone(Risk.step(p, 1.0, 1.05, 0.96, 1.0))
        self.assertEqual(Risk.step(p, 1.0, 1.0, 0.93, 0.95), 0.94)

    def test_gap_fills_at_open(self):
        p = self.pos()
        self.assertEqual(Risk.step(p, 0.80, 0.85, 0.78, 0.80), 0.80)

    def test_trail_arms_at_12(self):
        p = self.pos()
        Risk.step(p, 1.0, 1.11, 1.0, 1.10)
        self.assertFalse(p["trailing"])
        Risk.step(p, 1.10, 1.30, 1.10, 1.28)
        self.assertTrue(p["trailing"])
        self.assertAlmostEqual(p["stop"], 1.30 * 0.94)
        self.assertAlmostEqual(Risk.step(p, 1.25, 1.26, 1.20, 1.21), 1.30 * 0.94)


class FakeMarket:
    def __init__(self, pairs, reports, candles=None):
        self.pairs, self.reports, self.candles = pairs, reports, candles or {}

    def candidate_mints(self):
        return list(self.pairs), []

    def pairs_for(self, mints):
        return {m: self.pairs[m] for m in mints}

    def rug_report(self, mint):
        return self.reports[mint]

    def candles_1m(self, pool, limit=200):
        return self.candles.get(pool, [])

    def pair_now(self, pool):
        return next(p for p in self.pairs.values() if p["pair"] == pool)


class EndToEnd(unittest.TestCase):
    def test_pipeline(self):
        tmp = tempfile.mkdtemp()
        pairs = {
            "MINTgood": pair(),
            "MINTold": pair(mint="MINTold", symbol="OLD", pair="P2", age_h=100),
            "MINTthin": pair(mint="MINTthin", symbol="THIN", pair="P3", liq=10_000),
            "MINTmeh": pair(mint="MINTmeh", symbol="MEH", pair="P4", chg_h1=-5, chg_h6=-10, buys_h1=150, sells_h1=200),
        }
        reports = {m: rug() for m in pairs}
        mk = FakeMarket(pairs, reports)
        log = Logger(tmp, echo=False)
        led = Ledger(f"{tmp}/ledger.json")
        ex = Executor(led, log)
        d = {"scout": Scout(mk, log), "filter": Filter(mk, log), "analyst": Analyst(tmp, log, "/nonexistent"),
             "executor": ex, "risk": Risk(mk, led, ex, log), "ledger": led}
        scout_cycle(d)
        self.assertEqual([p["symbol"] for p in led.d["positions"].values()], ["GOOD"])
        pos = next(iter(led.d["positions"].values()))
        self.assertLessEqual(pos["cost"], C.PAPER_START_EQUITY * C.MAX_POSITION_PCT / 100 + 0.01)
        self.assertAlmostEqual(pos["stop"], pos["entry"] * 0.94)
        kills = open(f"{tmp}/kills.jsonl").read()
        for sym in ("OLD", "THIN", "MEH"):
            self.assertIn(sym, kills)
        # second cycle: no double buy
        scout_cycle(d)
        self.assertEqual(len(led.d["positions"]), 1)
        # price dumps through stop
        t = pos["entry_ts"] + 60
        mk.candles["POOLgood"] = [(t, pos["entry"], pos["entry"], pos["entry"] * 0.90, pos["entry"] * 0.91)]
        d["risk"].watch()
        self.assertEqual(len(led.d["positions"]), 0)
        c = led.d["closed"][0]
        self.assertEqual(c["reason"], "hard stop -6%")
        self.assertLess(c["pnl_pct"], -6)  # slippage + fees on top of the stop
        from grokbot.report import build_report
        self.assertIn("Paper equity", build_report(tmp))

    def test_executor_refuses_unarmed(self):
        tmp = tempfile.mkdtemp()
        ex = Executor(Ledger(f"{tmp}/l.json"), Logger(tmp, echo=False))
        sig = Signal(pair(), {}, 80, {})
        t = ex.ticket(sig)
        with self.assertRaises(TypeError):
            ex.fill(t)
        with self.assertRaises(TypeError):
            ex.fill(ArmedOrder(t, stop=None))
        with self.assertRaises(TypeError):
            Filter(None, None).run([pair()])


if __name__ == "__main__":
    unittest.main()
