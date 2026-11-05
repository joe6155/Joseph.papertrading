"""The six desk agents. Each one only accepts the previous agent's output type
(hard handoffs), so nothing can skip a stage:

  Scout -> Candidate -> Filter -> Survivor -> Analyst -> Signal
        -> Executor.ticket() -> Ticket -> Risk.arm() -> ArmedOrder -> Executor.fill()
  Risk watches open positions; Logger records every kill, fill and exit.
"""
import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict

from . import config as C
from .sources import DataError


# ---------------------------------------------------------------- handoff types
@dataclass(frozen=True)
class Candidate:
    pair: dict


@dataclass(frozen=True)
class Survivor:
    pair: dict
    safety: dict


@dataclass(frozen=True)
class Signal:
    pair: dict
    safety: dict
    score: float
    parts: dict


@dataclass(frozen=True)
class Ticket:
    signal: Signal
    notional: float
    expected_fill: float


@dataclass(frozen=True)
class ArmedOrder:
    ticket: Ticket
    stop: float
    armed_by: str = "Risk"


# ---------------------------------------------------------------- Logger
class Logger:
    def __init__(self, state_dir, echo=True):
        self.dir, self.echo = state_dir, echo
        os.makedirs(state_dir, exist_ok=True)

    def _w(self, name, rec):
        rec = {"ts": round(time.time()), **rec}
        with open(os.path.join(self.dir, name), "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def say(self, msg):
        if self.echo:
            print(time.strftime("%H:%M:%S"), msg, flush=True)

    def kill(self, stage, pair, reasons, **extra):
        self._w("kills.jsonl", {"stage": stage, "mint": pair.get("mint"), "symbol": pair.get("symbol"),
                                "reasons": reasons, **extra})

    def event(self, kind, **data):
        self._w("events.jsonl", {"kind": kind, **data})
        self.say(f"{kind}: " + ", ".join(f"{k}={v}" for k, v in data.items() if k in ("symbol", "price", "pnl_pct", "reason", "score", "notional", "error")))


# ---------------------------------------------------------------- Scout
class Scout:
    def __init__(self, market, log):
        self.market, self.log = market, log

    def run(self):
        mints, errors = self.market.candidate_mints()
        for e in errors:
            self.log.event("scout_feed_error", error=e)
        if not mints:
            raise DataError("no candidate feeds reachable")
        pairs = self.market.pairs_for(mints)
        out = []
        for mint in mints:
            p = pairs.get(mint)
            if not p:
                continue
            reasons = []
            if p["age_h"] is None or not (C.PAIR_AGE_MIN_H <= p["age_h"] <= C.PAIR_AGE_MAX_H):
                reasons.append(f"age {_r(p['age_h'])}h outside {C.PAIR_AGE_MIN_H}-{C.PAIR_AGE_MAX_H}h")
            if p["mcap"] is None or not (C.MCAP_MIN <= p["mcap"] <= C.MCAP_MAX):
                reasons.append(f"mcap ${_r(p['mcap'], 0)} outside ${C.MCAP_MIN:,}-${C.MCAP_MAX:,}")
            if p["price"] is None:
                reasons.append("no price")
            if reasons:
                self.log.kill("scout", p, reasons)
            else:
                out.append(Candidate(p))
        self.log.say(f"Scout: {len(mints)} mints seen, {len(out)} in age/mcap window")
        return out


# ---------------------------------------------------------------- Filter
def parse_rugcheck(rep, pair):
    """Turn a RugCheck report into the facts the Filter needs. None = unknown."""
    rep = rep or {}
    s = {"source": "rugcheck"}
    s["mint_revoked"] = (rep.get("mintAuthority") in (None, "")) if "mintAuthority" in rep else None
    s["freeze_revoked"] = (rep.get("freezeAuthority") in (None, "")) if "freezeAuthority" in rep else None

    lp = [_num((m.get("lp") or {}).get("lpLockedPct")) for m in rep.get("markets") or []]
    lp = [x for x in lp if x is not None]
    s["lp_locked_pct"] = max(lp) if lp else None

    known = rep.get("knownAccounts") or {}
    skip_types = {"AMM", "BURN", "LOCKER"}
    skip = {a for a, v in known.items() if str((v or {}).get("type", "")).upper() in skip_types}
    skip |= {pair.get("pair")}
    holders = rep.get("topHolders")
    creator = rep.get("creator")
    if holders is None:
        s["top10_pct"] = s["dev_pct"] = s["bundle_pct"] = None
        s["holder_owners"] = []
    else:
        real = [h for h in holders if h.get("address") not in skip and h.get("owner") not in skip]
        s["top10_pct"] = round(sum(_num(h.get("pct"), 0) for h in real[:10]), 2)
        dev = sum(_num(h.get("pct"), 0) for h in holders if creator and creator in (h.get("owner"), h.get("address")))
        supply = _num((rep.get("token") or {}).get("supply"))
        cb = _num(rep.get("creatorBalance"))
        if cb is not None and supply:
            dev = max(dev, cb / supply * 100)
        s["dev_pct"] = round(dev, 2) if creator else None
        if any("insider" in h for h in holders):
            ins = sum(_num(h.get("pct"), 0) for h in real if h.get("insider"))
            net = sum(_num(n.get("tokenAmount"), 0) for n in rep.get("insiderNetworks") or [])
            if net and supply:
                ins = max(ins, net / supply * 100)
            s["bundle_pct"] = round(ins, 2)
        else:
            s["bundle_pct"] = None
        s["holder_owners"] = [h.get("owner") for h in holders if h.get("owner")]

    risks = rep.get("risks") or []
    txt = " ".join(f"{r.get('name', '')} {r.get('description', '')}".lower() for r in risks)
    s["past_rugs"] = bool(rep.get("rugged")) or ("rug" in txt and ("creator" in txt or "dev" in txt))
    s["total_holders"] = _num(rep.get("totalHolders"))
    s["risk_names"] = [r.get("name") for r in risks]
    return s


class Filter:
    def __init__(self, market, log):
        self.market, self.log = market, log
        self._cache = {}

    def _safety(self, pair):
        hit = self._cache.get(pair["mint"])
        if hit and time.time() - hit[0] < C.RUGCHECK_CACHE_S:
            return hit[1]
        s = parse_rugcheck(self.market.rug_report(pair["mint"]), pair)
        self._cache[pair["mint"]] = (time.time(), s)
        return s

    @staticmethod
    def checks(p, s):
        """Return list of kill reasons (empty = pass)."""
        r = []

        def unknown(name):
            if C.KILL_ON_UNKNOWN:
                r.append(f"{name}: no data")

        # market-side checks (DexScreener)
        if p["liq"] < C.LIQ_MIN:
            r.append(f"liq ${p['liq']:,.0f} < ${C.LIQ_MIN:,}")
        if p["vol_h1"] < C.VOL_1H_MIN:
            r.append(f"1h vol ${p['vol_h1']:,.0f} < ${C.VOL_1H_MIN:,}")
        if p["chg_h1"] > C.PUMP_1H_MAX_PCT:
            r.append(f"already +{p['chg_h1']:.0f}% in 1h")
        # contract/holder checks (RugCheck)
        if s.get("mint_revoked") is None:
            unknown("mint authority")
        elif not s["mint_revoked"]:
            r.append("mint authority not revoked")
        if s.get("freeze_revoked") is None:
            unknown("freeze authority")
        elif not s["freeze_revoked"]:
            r.append("freeze authority not revoked")
        if s.get("lp_locked_pct") is None:
            unknown("LP lock")
        elif s["lp_locked_pct"] < C.LP_LOCKED_MIN_PCT:
            r.append(f"LP only {s['lp_locked_pct']:.0f}% burned/locked")
        if s.get("top10_pct") is None:
            unknown("top 10 holders")
        elif s["top10_pct"] > C.TOP10_MAX_PCT:
            r.append(f"top 10 hold {s['top10_pct']:.1f}% > {C.TOP10_MAX_PCT}%")
        if s.get("dev_pct") is None:
            unknown("dev holdings")
        elif s["dev_pct"] > C.DEV_MAX_PCT:
            r.append(f"dev holds {s['dev_pct']:.1f}% > {C.DEV_MAX_PCT}%")
        if s.get("past_rugs"):
            r.append("dev/creator rug history flagged")
        if s.get("bundle_pct") is None:
            unknown("bundle/insider share")
        elif s["bundle_pct"] > C.BUNDLE_MAX_PCT:
            r.append(f"bundled/insider {s['bundle_pct']:.1f}% > {C.BUNDLE_MAX_PCT}%")
        return r

    def run(self, candidates):
        out = []
        for c in candidates:
            if not isinstance(c, Candidate):
                raise TypeError("Filter only accepts Scout Candidates")
            p = c.pair
            try:
                s = self._safety(p)
            except DataError as e:
                self.log.kill("filter", p, [f"rugcheck unavailable: {e}"])
                continue
            reasons = self.checks(p, s)
            if reasons:
                self.log.kill("filter", p, reasons, safety={k: v for k, v in s.items() if k != "holder_owners"})
            else:
                out.append(Survivor(p, s))
        self.log.say(f"Filter: {len(out)}/{len(candidates)} survived")
        return out


# ---------------------------------------------------------------- Analyst
def _lin(x, lo, hi):
    if x is None:
        return None
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


class Analyst:
    def __init__(self, state_dir, log, smart_wallets_path=None):
        self.log = log
        self.snap_path = os.path.join(state_dir, "holder_snapshots.json")
        self.snaps = _load(self.snap_path, {})
        path = smart_wallets_path or os.path.join(os.path.dirname(__file__), "smart_wallets.txt")
        self.smart = set()
        if os.path.exists(path):
            with open(path) as f:
                self.smart = {ln.split("#")[0].strip() for ln in f if ln.split("#")[0].strip()}

    def _holder_growth(self, mint, n):
        """% holder growth per hour vs the oldest snapshot 10min-6h ago."""
        now = time.time()
        hist = [x for x in self.snaps.get(mint, []) if now - x[0] < 6 * 3600]
        growth = None
        older = [x for x in hist if now - x[0] >= 600]
        if n and older and older[0][1]:
            t0, n0 = older[0]
            growth = (n - n0) / n0 * 100 / ((now - t0) / 3600)
        if n:
            hist.append([round(now), n])
        self.snaps[mint] = hist[-40:]
        return growth

    def score(self, p, s):
        m = 0.6 * _lin(p["chg_h1"], 0, 30) + 0.3 * _lin(p["chg_h6"], 0, 60) + 0.1 * (1.0 if p["chg_m5"] > 0 else 0.0)
        if p["chg_h1"] < 0:
            m *= 0.5
        bs = p["buys_h1"] / p["sells_h1"] if p["sells_h1"] else (2.0 if p["buys_h1"] else None)
        liq_mcap = p["liq"] / p["mcap"] if p["mcap"] else None
        growth = self._holder_growth(p["mint"], s.get("total_holders"))
        parts = {
            "momentum": m,
            "unique_buyers": _lin(p["buys_h1"], 100, 1000),
            "buy_sell": _lin(bs, 0.9, 1.6),
            "holder_growth": _lin(growth, 0, 10),
            "liq_mcap": _lin(liq_mcap, 0.05, 0.20),
            "smart_wallets": (_lin(len(self.smart & set(s.get("holder_owners") or [])), 0, 3) if self.smart else None),
        }
        used = {k: v for k, v in parts.items() if v is not None}
        wsum = sum(C.WEIGHTS[k] for k in used)
        score = 100 * sum(C.WEIGHTS[k] * v for k, v in used.items()) / wsum if wsum else 0
        detail = {k: (round(v * 100) if v is not None else "n/a") for k, v in parts.items()}
        detail["_raw"] = {"buy_sell": _r(bs), "liq_mcap": _r(liq_mcap, 3), "holder_growth_pct_h": _r(growth)}
        return round(score, 1), detail

    def run(self, survivors):
        out = []
        for sv in survivors:
            if not isinstance(sv, Survivor):
                raise TypeError("Analyst only accepts Filter Survivors")
            sc, parts = self.score(sv.pair, sv.safety)
            if sc >= C.SCORE_PASS:
                out.append(Signal(sv.pair, sv.safety, sc, parts))
            else:
                self.log.kill("analyst", sv.pair, [f"score {sc} < {C.SCORE_PASS}"], parts=parts)
        _save(self.snap_path, self.snaps)
        out.sort(key=lambda x: -x.score)
        self.log.say(f"Analyst: {len(out)}/{len(survivors)} scored {C.SCORE_PASS}+")
        return out


# ---------------------------------------------------------------- Ledger
class Ledger:
    def __init__(self, path):
        self.path = path
        self.d = _load(path, None) or {
            "start_equity": C.PAPER_START_EQUITY, "cash": C.PAPER_START_EQUITY,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "positions": {}, "closed": []}

    def save(self):
        _save(self.path, self.d)

    def equity(self):
        return self.d["cash"] + sum(p["qty"] * p["last"] for p in self.d["positions"].values())


def slippage_pct(notional, liq):
    return C.BASE_SLIPPAGE_PCT + C.IMPACT_MULT * (notional / liq * 100 if liq else 5)


# ---------------------------------------------------------------- Executor
class Executor:
    def __init__(self, ledger, log):
        self.ledger, self.log = ledger, log

    def ticket(self, sig):
        if not isinstance(sig, Signal):
            raise TypeError("Executor only accepts Analyst Signals")
        L = self.ledger.d
        mint = sig.pair["mint"]
        if any(p["mint"] == mint for p in L["positions"].values()):
            self.log.kill("executor", sig.pair, ["already holding"])
            return None
        recent = [c for c in L["closed"] if c["mint"] == mint and time.time() - c["exit_ts"] < C.REENTRY_COOLDOWN_H * 3600]
        if recent:
            self.log.kill("executor", sig.pair, [f"exited < {C.REENTRY_COOLDOWN_H}h ago"])
            return None
        notional = min(self.ledger.equity() * C.MAX_POSITION_PCT / 100, L["cash"])
        if notional < 25:
            self.log.kill("executor", sig.pair, [f"not enough cash (${L['cash']:.2f})"])
            return None
        fill = sig.pair["price"] * (1 + slippage_pct(notional, sig.pair["liq"]) / 100)
        return Ticket(sig, round(notional, 2), fill)

    def fill(self, order):
        if not isinstance(order, ArmedOrder) or order.armed_by != "Risk" or not order.stop:
            raise TypeError("Executor will not fill an order without a Risk-armed stop")
        t, p = order.ticket, order.ticket.signal.pair
        cap = self.ledger.equity() * C.MAX_POSITION_PCT / 100 + 0.01
        if t.notional > cap or t.notional > self.ledger.d["cash"] + 0.01:
            raise ValueError("ticket breaks the 8% / cash limit")
        qty = t.notional * (1 - C.FEE_PCT / 100) / t.expected_fill
        pid = uuid.uuid4().hex[:8]
        pos = {"id": pid, "mint": p["mint"], "symbol": p["symbol"], "pair": p["pair"], "url": p.get("url"),
               "entry_ts": round(time.time()), "entry": t.expected_fill, "quote_at_entry": p["price"],
               "qty": qty, "cost": t.notional, "stop": order.stop, "initial_stop": order.stop,
               "peak": t.expected_fill, "trailing": False, "last": p["price"], "last_ts": round(time.time()),
               "liq_at_entry": p["liq"], "score": t.signal.score, "parts": t.signal.parts}
        self.ledger.d["cash"] -= t.notional
        self.ledger.d["positions"][pid] = pos
        self.ledger.save()
        self.log.event("entry", symbol=p["symbol"], mint=p["mint"], price=_sig(t.expected_fill), notional=t.notional,
                       stop=_sig(order.stop), score=t.signal.score, parts=t.signal.parts)
        return pos

    def close(self, pos, price, reason):
        slip = slippage_pct(pos["qty"] * price, pos.get("liq_at_entry"))
        px = price * (1 - slip / 100)
        proceeds = pos["qty"] * px * (1 - C.FEE_PCT / 100)
        L = self.ledger.d
        L["cash"] += proceeds
        rec = {**{k: pos[k] for k in ("id", "mint", "symbol", "entry_ts", "entry", "cost", "initial_stop", "peak", "score")},
               "exit_ts": round(time.time()), "exit_trigger": price, "exit": px, "proceeds": round(proceeds, 2),
               "pnl": round(proceeds - pos["cost"], 2), "pnl_pct": round((proceeds / pos["cost"] - 1) * 100, 2),
               "reason": reason}
        L["closed"].append(rec)
        del L["positions"][pos["id"]]
        self.ledger.save()
        self.log.event("exit", symbol=pos["symbol"], price=_sig(px), pnl_pct=rec["pnl_pct"], reason=reason, pnl=rec["pnl"])
        return rec


# ---------------------------------------------------------------- Risk
class Risk:
    def __init__(self, market, ledger, executor, log):
        self.market, self.ledger, self.executor, self.log = market, ledger, executor, log

    def arm(self, ticket):
        if not isinstance(ticket, Ticket):
            raise TypeError("Risk only arms Executor Tickets")
        return ArmedOrder(ticket, stop=ticket.expected_fill * (1 - C.STOP_PCT / 100))

    @staticmethod
    def step(pos, o, h, l, c):
        """Apply one candle. Returns exit trigger price or None. Stop checked before
        the candle's high can raise it (conservative)."""
        if l is not None and l <= pos["stop"]:
            return o if o is not None and o < pos["stop"] else pos["stop"]
        if h is not None and h > pos["peak"]:
            pos["peak"] = h
        if pos["peak"] >= pos["entry"] * (1 + C.TRAIL_ARM_PCT / 100):
            pos["trailing"] = True
        if pos["trailing"]:
            pos["stop"] = max(pos["stop"], pos["peak"] * (1 - C.TRAIL_PCT / 100))
        if c is not None and c <= pos["stop"]:
            return c
        return None

    def watch(self):
        for pos in list(self.ledger.d["positions"].values()):
            bars = []
            try:
                bars = [b for b in self.market.candles_1m(pos["pair"]) if b[0] + 60 > pos["last_ts"]]
            except DataError as e:
                self.log.event("risk_data_error", symbol=pos["symbol"], error=str(e))
            if not bars:  # fall back to a spot quote
                try:
                    now = self.market.pair_now(pos["pair"])
                except DataError as e:
                    self.log.event("risk_data_error", symbol=pos["symbol"], error=str(e))
                    continue
                if not now or now["price"] is None:
                    continue
                pos["liq_at_entry"] = now["liq"] or pos["liq_at_entry"]
                bars = [(round(time.time()), now["price"], now["price"], now["price"], now["price"])]
            exit_px = None
            for ts, o, h, l, c in bars:
                if ts < pos["entry_ts"] and len(bars) > 1:
                    continue  # candle opened before we bought; its low isn't ours
                exit_px = self.step(pos, o, h, l, c)
                pos["last"], pos["last_ts"] = (exit_px or c or pos["last"]), ts
                if exit_px:
                    break
            if exit_px:
                self.executor.close(pos, exit_px, "trail stop" if pos["trailing"] else "hard stop -6%")
        self.ledger.save()


# ---------------------------------------------------------------- utils
def _num(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _r(x, n=1):
    return None if x is None else round(x, n)


def _sig(x):
    return float(f"{x:.4g}") if x else x


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)
