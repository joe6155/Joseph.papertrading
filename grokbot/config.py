"""Grok Bot desk rules. Paper trading only: no wallet, no keys, no real orders."""

PAPER_START_EQUITY = 10_000.0  # USD

# --- Scout -------------------------------------------------------------
PAIR_AGE_MIN_H = 0.5          # 30 minutes
PAIR_AGE_MAX_H = 72.0
MCAP_MIN = 300_000
MCAP_MAX = 20_000_000

# --- Filter (any failure = kill) --------------------------------------
LIQ_MIN = 40_000
VOL_1H_MIN = 50_000
TOP10_MAX_PCT = 25.0          # excludes AMM/LP and burn accounts
DEV_MAX_PCT = 3.0
BUNDLE_MAX_PCT = 10.0         # insider/bundled share of supply (RugCheck proxy)
PUMP_1H_MAX_PCT = 80.0        # already up more than this in 1h = too late
LP_LOCKED_MIN_PCT = 95.0      # "burned or locked"
KILL_ON_UNKNOWN = True        # a check with missing data counts as a fail

# --- Analyst -----------------------------------------------------------
SCORE_PASS = 70
WEIGHTS = {                   # renormalised over components that have data
    "momentum": 20,
    "unique_buyers": 20,      # proxy: 1h buy-txn count (DexScreener)
    "buy_sell": 15,
    "holder_growth": 15,      # from RugCheck holder snapshots between scans
    "liq_mcap": 15,
    "smart_wallets": 15,      # off until grokbot/smart_wallets.txt exists
}

# --- Executor ----------------------------------------------------------
MAX_POSITION_PCT = 8.0        # of equity, per trade
FEE_PCT = 0.25                # per side, DEX fee
BASE_SLIPPAGE_PCT = 1.0       # per side, plus size/liquidity impact below
IMPACT_MULT = 2.0             # extra slippage % = IMPACT_MULT * (size / liquidity) * 100
REENTRY_COOLDOWN_H = 6        # no re-buying a token this soon after exiting it

# --- Risk --------------------------------------------------------------
STOP_PCT = 6.0                # hard stop, set before the entry fills
TRAIL_ARM_PCT = 12.0          # trailing starts once peak is +12% over entry
TRAIL_PCT = 6.0               # trail distance below the peak (not specified; tune here)

# --- Loop --------------------------------------------------------------
RISK_INTERVAL_S = 60          # check open positions
SCOUT_INTERVAL_S = 300        # look for new coins
RUGCHECK_CACHE_S = 600
