"""Desk rules. Everything the crew does is checked against these."""

STARTING_CAPITAL = 10_000.0
MAX_RISK_PER_TRADE = 0.01      # 1% of equity at risk (entry -> stop)
MAX_POSITION_PCT = 0.20        # no single position above 20% of equity
MAX_OPEN_POSITIONS = 6
ALLOW_SHORT = False
ALLOW_LEVERAGE = False         # total cost of positions can never exceed cash
ALLOW_OPTIONS = False

# Swing holding period
MAX_HOLD_BARS = 30             # time stop (trading days / days for crypto)
ATR_STOP_MULT = 2.0
TARGET_R = 3.0                 # take profit at 3R

# Universe — only spot instruments and unleveraged ETFs
UNIVERSE = {
    "stocks_etfs": ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLI",
                    "XLY", "XLP", "XLU", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"],
    "crypto_spot": ["BTC-USD", "ETH-USD", "SOL-USD"],
    "futures_via_etf": ["GLD", "SLV", "USO", "UNG", "DBA", "TLT", "IEF"],   # gold, silver, oil, natgas, ags, bonds
    "forex_via_etf": ["UUP", "FXE", "FXY", "FXB"],                          # dollar, euro, yen, pound
}

# Hard blocklist: leveraged / inverse products. Builder rejects anything here
# or anything whose name looks leveraged/inverse.
BLOCKED = {"TQQQ", "SQQQ", "SPXL", "SPXS", "UPRO", "SPXU", "SOXL", "SOXS", "TNA", "TZA",
           "UCO", "SCO", "UGL", "GLL", "AGQ", "ZSL", "BOIL", "KOLD", "TMF", "TMV",
           "SH", "PSQ", "DOG", "RWM", "BITX", "BITI", "ETHU", "SBIT", "YCS", "YCL",
           "ULE", "EUO", "SSO", "QLD", "SDS", "QID", "UVXY", "SVXY", "VXX"}
LEVERAGE_WORDS = ("2x", "3x", "ultra", "inverse", "bear", "short", "-1x", "leveraged")


def all_symbols():
    return [s for group in UNIVERSE.values() for s in group]


def asset_class(symbol):
    for cls, syms in UNIVERSE.items():
        if symbol in syms:
            return cls
    return "unknown"
