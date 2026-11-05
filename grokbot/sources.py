"""Read-only market data: DexScreener, GeckoTerminal, RugCheck. Stdlib only.

Modes:
  live   - hit the APIs
  record - hit the APIs and save every response to a folder
  replay - serve saved responses only (no network), for tests and debugging
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

DEX = "https://api.dexscreener.com"
GECKO = "https://api.geckoterminal.com/api/v2"
RUG = "https://api.rugcheck.xyz/v1"

# Per-host minimum spacing between calls (seconds), under each API's public limit.
_SPACING = {"api.dexscreener.com": 0.25, "api.geckoterminal.com": 2.1, "api.rugcheck.xyz": 0.6}


class DataError(Exception):
    pass


class Http:
    def __init__(self, mode="live", folder=None, timeout=20):
        self.mode, self.folder, self.timeout = mode, folder, timeout
        self._last = {}
        if folder:
            os.makedirs(folder, exist_ok=True)

    def _path(self, url):
        return os.path.join(self.folder, hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")

    def get(self, url):
        if self.mode == "replay":
            p = self._path(url)
            if not os.path.exists(p):
                raise DataError(f"no recording for {url}")
            with open(p) as f:
                return json.load(f)["body"]
        host = url.split("/")[2]
        wait = _SPACING.get(host, 0.2) - (time.time() - self._last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        body = None
        for attempt in range(3):
            self._last[host] = time.time()
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "grokbot-paper/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = json.loads(r.read().decode())
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    time.sleep(2 * (attempt + 1) ** 2)
                    continue
                raise DataError(f"HTTP {e.code} for {url}") from e
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                if attempt == 2:
                    raise DataError(f"{type(e).__name__} for {url}: {e}") from e
                time.sleep(2)
        if body is None:
            raise DataError(f"gave up on {url}")
        if self.mode == "record":
            with open(self._path(url), "w") as f:
                json.dump({"url": url, "ts": time.time(), "body": body}, f)
        return body


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class Market:
    def __init__(self, http):
        self.http = http

    # ---- discovery ----------------------------------------------------
    def candidate_mints(self):
        """Fresh Solana token mints from several public feeds, de-duplicated."""
        mints, errors = [], []

        def add(m):
            if m and m not in mints:
                mints.append(m)

        for path in ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"):
            try:
                for t in self.http.get(DEX + path) or []:
                    if t.get("chainId") == "solana":
                        add(t.get("tokenAddress"))
            except DataError as e:
                errors.append(str(e))
        for path in ("/networks/solana/new_pools?page=1", "/networks/solana/new_pools?page=2",
                     "/networks/solana/trending_pools?page=1"):
            try:
                for p in (self.http.get(GECKO + path) or {}).get("data", []):
                    tid = (((p.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id", "")
                    if tid.startswith("solana_"):
                        add(tid[len("solana_"):])
            except DataError as e:
                errors.append(str(e))
        return mints, errors

    def pairs_for(self, mints):
        """Best (deepest-liquidity) Solana pair per mint, normalised."""
        best = {}
        for i in range(0, len(mints), 30):
            chunk = mints[i:i + 30]
            data = self.http.get(f"{DEX}/tokens/v1/solana/{','.join(chunk)}") or []
            for p in data:
                if p.get("chainId") != "solana":
                    continue
                mint = (p.get("baseToken") or {}).get("address")
                if mint not in chunk:
                    continue  # token is the quote side of this pair
                liq = _f((p.get("liquidity") or {}).get("usd"), 0)
                if mint not in best or liq > best[mint]["liq"]:
                    best[mint] = normalise_pair(p)
        return best

    def pair_now(self, pair_address):
        d = self.http.get(f"{DEX}/latest/dex/pairs/solana/{pair_address}") or {}
        ps = d.get("pairs") or ([d["pair"]] if d.get("pair") else [])
        return normalise_pair(ps[0]) if ps else None

    # ---- safety -------------------------------------------------------
    def rug_report(self, mint):
        return self.http.get(f"{RUG}/tokens/{mint}/report")

    # ---- prices for stop checks ---------------------------------------
    def candles_1m(self, pool, limit=200):
        """[(ts, open, high, low, close)] oldest first, USD."""
        d = self.http.get(f"{GECKO}/networks/solana/pools/{pool}/ohlcv/minute?aggregate=1&limit={limit}&currency=usd")
        rows = (((d or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        out = [(int(r[0]), _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4])) for r in rows if len(r) >= 5]
        return sorted(out)


def normalise_pair(p):
    tx = p.get("txns") or {}
    vol = p.get("volume") or {}
    chg = p.get("priceChange") or {}
    created = p.get("pairCreatedAt")
    return {
        "mint": (p.get("baseToken") or {}).get("address"),
        "symbol": (p.get("baseToken") or {}).get("symbol", "?"),
        "name": (p.get("baseToken") or {}).get("name", ""),
        "pair": p.get("pairAddress"),
        "dex": p.get("dexId"),
        "url": p.get("url"),
        "price": _f(p.get("priceUsd")),
        "liq": _f((p.get("liquidity") or {}).get("usd"), 0),
        "mcap": _f(p.get("marketCap")) or _f(p.get("fdv")),
        "vol_h1": _f(vol.get("h1"), 0),
        "chg_m5": _f(chg.get("m5"), 0),
        "chg_h1": _f(chg.get("h1"), 0),
        "chg_h6": _f(chg.get("h6"), 0),
        "buys_h1": int((tx.get("h1") or {}).get("buys") or 0),
        "sells_h1": int((tx.get("h1") or {}).get("sells") or 0),
        "age_h": (time.time() * 1000 - created) / 3_600_000 if created else None,
    }
