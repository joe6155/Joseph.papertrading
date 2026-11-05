"""Summarise the paper desk: python3 -m grokbot.report"""
import collections
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def _bucket(reason):
    """'liq $12,000 < $40,000' -> 'liq < min' style buckets for tallies."""
    r = re.sub(r"[-+]?\$?[\d,.]+%?", "#", reason)
    return re.sub(r"\s+", " ", r).strip()


def build_report(state):
    with open(os.path.join(state, "ledger.json")) as f:
        L = json.load(f)
    kills = _jsonl(os.path.join(state, "kills.jsonl"))
    events = _jsonl(os.path.join(state, "events.jsonl"))
    pos = L["positions"].values()
    eq = L["cash"] + sum(p["qty"] * p["last"] for p in pos)
    chg = (eq / L["start_equity"] - 1) * 100
    closed = L["closed"]
    wins = [c for c in closed if c["pnl"] > 0]
    out = [f"# Grok Bot paper desk — {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}", "",
           f"**Paper equity ${eq:,.2f}** ({chg:+.2f}% since {L['created'][:10]}, start ${L['start_equity']:,.0f}) · cash ${L['cash']:,.2f}", ""]
    if closed:
        out.append(f"Closed {len(closed)} · win rate {len(wins) / len(closed) * 100:.0f}% · realised ${sum(c['pnl'] for c in closed):+,.2f}")
        out += ["", "## Exits", "| Coin | P&L % | P&L $ | Reason | Held |", "|---|---|---|---|---|"]
        for c in closed[-25:]:
            held = (c["exit_ts"] - c["entry_ts"]) / 3600
            out.append(f"| {c['symbol']} | {c['pnl_pct']:+.1f}% | ${c['pnl']:+,.2f} | {c['reason']} | {held:.1f}h |")
    out += ["", "## Open positions"]
    if pos:
        out += ["| Coin | Cost | Entry | Last | Unreal % | Stop | Trailing |", "|---|---|---|---|---|---|---|"]
        for p in pos:
            u = (p["last"] / p["entry"] - 1) * 100
            out.append(f"| {p['symbol']} | ${p['cost']:,.0f} | {p['entry']:.4g} | {p['last']:.4g} | {u:+.1f}% | {p['stop']:.4g} | {'yes' if p['trailing'] else 'no'} |")
    else:
        out.append("None.")
    entries = [e for e in events if e["kind"] == "entry"]
    out += ["", f"## Entries ({len(entries)})"]
    for e in entries[-25:]:
        out.append(f"- {e['symbol']} score {e['score']} · ${e['notional']:,.0f} @ {e['price']} · stop {e['stop']}")
    stages = collections.Counter(k["stage"] for k in kills)
    reasons = collections.Counter(_bucket(r) for k in kills for r in k["reasons"])
    out += ["", f"## Kills ({len(kills)} logged: " + ", ".join(f"{s} {n}" for s, n in stages.most_common()) + ")"]
    for r, n in reasons.most_common(15):
        out.append(f"- {n} × {r}")
    near = sorted((k for k in kills if k["stage"] == "analyst"),
                  key=lambda k: -float(re.findall(r"[\d.]+", k["reasons"][0])[0]))[:5]
    if near:
        out += ["", "Closest misses: " + ", ".join(f"{k['symbol']} ({k['reasons'][0].split(' <')[0]})" for k in near)]
    errs = [e for e in events if e["kind"].endswith("error")]
    if errs:
        out += ["", f"Data errors: {len(errs)} (last: {errs[-1].get('error', '')[:120]})"]
    out += ["", "_Paper trading only. Fills include modelled fees and slippage, but real memecoin fills (MEV, sniper bots, thin books) are usually worse._"]
    return "\n".join(out)


def write_report(state):
    os.makedirs(os.path.join(HERE, "reports"), exist_ok=True)
    path = os.path.join(HERE, "reports", f"report_{time.strftime('%Y-%m-%d_%H%M', time.gmtime())}.md")
    with open(path, "w") as f:
        f.write(build_report(state) + "\n")
    return path


if __name__ == "__main__":
    state = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "state")
    print(build_report(state))
