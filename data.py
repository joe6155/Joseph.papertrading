"""Yahoo Finance daily bars (query2 host; query1 is not on the allowlist)."""
import json
import time
import urllib.request

import pandas as pd

HOSTS = ["query2.finance.yahoo.com", "query1.finance.yahoo.com"]
UA = {"User-Agent": "Mozilla/5.0 (paper-trading-desk)"}


def _get(path):
    last = None
    for host in HOSTS:
        try:
            req = urllib.request.Request(f"https://{host}{path}", headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except Exception as e:  # try the next host
            last = e
    raise RuntimeError(f"Yahoo request failed: {last}")


def bars(symbol, range_="2y", interval="1d"):
    """Return a DataFrame of daily OHLCV indexed by date, NaN rows dropped."""
    js = _get(f"/v8/finance/chart/{symbol}?range={range_}&interval={interval}")
    res = js["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({
        "open": q["open"], "high": q["high"], "low": q["low"],
        "close": q["close"], "volume": q["volume"],
    }, index=pd.to_datetime(res["timestamp"], unit="s").normalize())
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df[~df.index.duplicated(keep="last")]


def bars_many(symbols, range_="2y"):
    out = {}
    for s in symbols:
        try:
            out[s] = bars(s, range_)
        except Exception as e:
            print(f"  ! {s}: {e}")
        time.sleep(0.15)  # be polite
    return out
