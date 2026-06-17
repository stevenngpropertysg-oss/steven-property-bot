"""
sgx_scanner.py
Fully Dynamic SGX Stock Scanner — powered by EODData universe file
- Reads Symbols_SGX.txt from repo (771 SGX equities, warrants excluded)
- Pre-filters by market cap > SGD 50M and volume > 100K/day
- Scores ~100-150 survivors on fundamentals + macro overlay
- Returns top 30 and top 3 for TradingAgents analysis
 
To update universe: download new Symbols_SGX.txt from eoddata.com/stocklist/SGX
and upload to repo root. No code changes needed.
"""
 
import yfinance as yf
import json
import time
import os
import requests
from datetime import datetime
 
# ── LOAD SGX UNIVERSE FROM EODDATA FILE ──────────────────────────────
def load_sgx_universe():
    """
    Load full SGX equity universe from Symbols_SGX.txt
    File format: Symbol\tDescription (tab-separated, first line is header)
    Warrants excluded automatically (contain 'W.SI')
    Falls back to core blue chips if file not found
    """
    # Try local file (available after GitHub Actions checkout)
    local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Symbols_SGX.txt')
 
    if os.path.exists(local_path):
        tickers = []
        with open(local_path, 'r') as f:
            lines = f.readlines()
        for line in lines[1:]:  # skip header
            parts = line.strip().split('\t')
            if not parts:
                continue
            ticker = parts[0].strip()
            # Keep genuine equities only — exclude warrants and structured products
            if not ticker.endswith('.SI'):
                continue
            base = ticker.replace('.SI', '')
            name = parts[1].strip() if len(parts) > 1 else ''
            # Exclude warrants
            if base.endswith('W') or base.endswith('WW'):
                continue
            # Exclude structured products (S/B suffix with letter before it)
            if len(base) >= 4 and base[-1] in ['S','B'] and not base[-2].isdigit():
                continue
            # Exclude by name keywords
            name_l = name.lower()
            if any(x in name_l for x in ['short','long','warrant','cbbc','mcw',
                                          'becw','ecw','xlong','xshort']):
                continue
            tickers.append(ticker)
        print(f"  Loaded {len(tickers)} SGX equities from Symbols_SGX.txt")
        return tickers
 
    # Fallback: core blue chips if file missing
    print("  WARNING: Symbols_SGX.txt not found — using core blue chip fallback")
    return [
        "D05.SI","O39.SI","U11.SI","S68.SI","C38U.SI","A17U.SI","C6L.SI",
        "Z74.SI","S63.SI","C52.SI","G13.SI","BN4.SI","U96.SI","S58.SI",
        "F34.SI","V03.SI","C09.SI","H78.SI","580.SI","Q0F.SI","EB5.SI",
        "F99.SI","BS6.SI","8YZ.SI","P9D.SI","43A.SI","558.SI","1D0.SI",
        "9CI.SI","G07.SI","ME8U.SI","N2IU.SI","BUOU.SI","K71U.SI","M44U.SI",
    ]
 
# ── DIVIDEND YIELD NORMALISATION + SANITY CHECK ───────────────────────
def _normalise_and_log_dividend_yield(ticker, raw):
    """
    Normalise Yahoo's dividendYield field to a true decimal fraction
    (e.g. 2.4% stored as 0.024).
 
    CONFIRMED ROOT CAUSE (17 Jun 2026, via Yahoo Finance UI directly): OKP
    Holdings' real "Forward Dividend & Yield" is 0.01 (0.87%) — i.e. the
    true yield is well under 1%. The raw yfinance dividendYield value for
    this ticker is a number like 0.87 (already in percentage-point form,
    meaning "0.87%"), NOT a fraction like 0.0087. The old "if raw > 1,
    divide by 100" heuristic assumed any value under 1 must already be a
    clean decimal fraction (e.g. 0.024 = 2.4%) — but 0.87 is numerically
    indistinguishable from that case using a threshold alone: there is no
    way to tell "0.0087 meaning 0.87%" apart from "0.87 meaning 0.87%"
    just by looking at the number. The threshold guess silently treated
    0.87 as if it meant "87% as a fraction" and returned it unconverted,
    which downstream rendering (a plain *100) then displayed as "87.0%".
 
    FIX: since a single per-ticker number can't disambiguate this on its
    own, treat plausibility as the deciding signal instead of magnitude
    alone. Real SGX dividend yields cluster roughly 0-12%, occasionally
    up to ~20% in a genuine one-off special-dividend year. We try the
    standard decimal-fraction interpretation first (raw as-is if < 1, else
    raw/100); if that interpretation is itself implausible (outside a wide
    sanity band), we instead try the alternate interpretation (raw is
    already in percentage-point form, e.g. 0.87 meaning 0.87%) before
    falling back to a hard clamp. Every anomaly is logged with the raw
    value so future tickers with this issue are visible, not silent.
    """
    SANITY_FLOOR_PCT = 0.0
    SANITY_CEILING_PCT = 25.0  # generous; no real SGX yield should exceed this
 
    if not raw:
        return 0
 
    # Interpretation A: standard decimal-fraction guess (old behaviour)
    interp_a_decimal = raw / 100 if raw > 1 else raw
    interp_a_pct = interp_a_decimal * 100
 
    if SANITY_FLOOR_PCT <= interp_a_pct <= SANITY_CEILING_PCT:
        return interp_a_decimal
 
    # Interpretation A was implausible — try treating raw as if it's
    # ALREADY in percentage-point form (e.g. 0.87 meaning 0.87%, not 87%).
    interp_b_decimal = raw / 100
    interp_b_pct = interp_b_decimal * 100  # == raw, just for clarity below
 
    if SANITY_FLOOR_PCT <= interp_b_pct <= SANITY_CEILING_PCT:
        print(f"  ℹ DIVIDEND YIELD CORRECTED [{ticker}]: raw yfinance value = {raw} "
              f"-> standard interpretation gave implausible {interp_a_pct:.1f}%, "
              f"using percentage-point interpretation instead = {interp_b_pct:.2f}%")
        return interp_b_decimal
 
    # Neither interpretation is plausible — log raw value and clamp.
    print(f"  ⚠ DIVIDEND YIELD ANOMALY [{ticker}]: raw yfinance value = {raw} "
          f"-> both interpretations implausible (would be {interp_a_pct:.1f}% or "
          f"{interp_b_pct:.1f}%). Clamping to {SANITY_CEILING_PCT}% — "
          f"investigate raw Yahoo data for this ticker.")
    return SANITY_CEILING_PCT / 100
 
# ── FETCH STOCK DATA ─────────────────────────────────────────────────
def get_stock_data(ticker):
    """Fetch fundamentals — returns None if delisted or no data"""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        if not info or len(info) < 5:
            return None
 
        hist = stock.history(period="3mo")
        if hist.empty:
            return None
 
        momentum = 0
        if len(hist) >= 10:
            momentum = (hist['Close'].iloc[-1] - hist['Close'].iloc[0]) / hist['Close'].iloc[0] * 100
 
        avg_vol = float(hist['Volume'].mean()) if not hist.empty else 0
 
        def safe(val):
            try:
                return float(val) if val is not None else None
            except:
                return None
 
        return {
            'ticker': ticker,
            'name': info.get('longName', info.get('shortName', ticker)),
            'price': safe(info.get('currentPrice') or info.get('regularMarketPrice')) or 0,
            'market_cap': safe(info.get('marketCap')) or 0,
            'pe_ratio': safe(info.get('trailingPE')),
            'pb_ratio': safe(info.get('priceToBook')),
            # ── DIVIDEND YIELD — NORMALISED ONCE, HERE, TO A TRUE DECIMAL ──
            # Yahoo's `dividendYield` field via yfinance has been inconsistent
            # across versions/tickers: sometimes a decimal fraction (0.024
            # meaning 2.4%), sometimes already a whole percentage number
            # (2.4 meaning 2.4%). We normalise ONCE here. HOWEVER: testing
            # against live data (17 Jun 2026 run) showed OKP (5CF.SI) and
            # YZJ Maritime (8YZ.SI) still rendering as 87.0% / 80.0% dividend
            # yield even after this fix — both companies' TRUE yield is
            # roughly 2-3%. This means Yahoo's raw dividendYield field is
            # itself returning a wrong/unstable number for some tickers
            # (e.g. possibly double-counting a recent special dividend, or
            # a units bug on Yahoo's side) — not just an ambiguous decimal
            # vs. percentage format we can resolve with a smarter guess.
            # No legitimate SGX blue-chip/mid-cap dividend yield is anywhere
            # near 80-90% — that is always bad data, never a real yield.
            # FIX: normalise the decimal/percentage ambiguity as before, but
            # ALSO sanity-cap at 25% (generous upper bound for even the most
            # extreme one-off special-dividend year) and log every case
            # where the raw value looked implausible, so we can see exactly
            # what Yahoo is sending and decide whether to special-case it
            # or fall back to manually-sourced dividend data for the
            # affected tickers.
            'dividend_yield': (lambda raw: _normalise_and_log_dividend_yield(ticker, raw))(safe(info.get('dividendYield'))),
            'debt_to_equity': safe(info.get('debtToEquity')),
            'revenue': safe(info.get('totalRevenue')) or 0,
            'profit_margin': safe(info.get('profitMargins')),
            'avg_volume': avg_vol,
            'momentum_3m': momentum,
            'sector': info.get('sector', 'Unknown'),
            'industry': info.get('industry', 'Unknown'),
        }
    except:
        return None
 
# ── PRE-FILTER ───────────────────────────────────────────────────────
def filter_stocks(stocks):
    """
    Pre-filter: keep only liquid, real businesses
    - Market Cap > SGD 50M (no micro-caps)
    - Avg Daily Volume > 100K shares (can trade without slippage)
    - Revenue > SGD 10M (real operating business)
    - P/E not deeply negative (not severe loss-maker)
    """
    filtered = []
    stats = {'market_cap': 0, 'volume': 0, 'revenue': 0, 'negative_pe': 0}
    for s in stocks:
        if not s:
            continue
        if s['market_cap'] < 50_000_000:
            stats['market_cap'] += 1
            continue
        if s['avg_volume'] < 100_000:
            stats['volume'] += 1
            continue
        if s['revenue'] < 10_000_000:
            stats['revenue'] += 1
            continue
        pe = s['pe_ratio']
        if pe and pe < -10:
            stats['negative_pe'] += 1
            continue
        filtered.append(s)
    print(f"  Pre-filter removed: mkt_cap={stats['market_cap']}, volume={stats['volume']}, revenue={stats['revenue']}, neg_pe={stats['negative_pe']}")
    return filtered
 
# ── SCORE ────────────────────────────────────────────────────────────
def score_stock(stock, macro_context):
    """Score 0-100 — higher = better buy candidate for SGX retail investor"""
    score = 50
 
    pe = stock['pe_ratio']
    pb = stock['pb_ratio']
    # dividend_yield is ALREADY a clean decimal fraction (e.g. 0.024 = 2.4%)
    # from get_stock_data() — do NOT re-guess or re-convert here, just scale
    # to a percentage for the scoring thresholds below with a single *100.
    div = (stock['dividend_yield'] or 0) * 100
    de = stock['debt_to_equity']
    mom = stock['momentum_3m']
    pm = stock['profit_margin']
 
    # P/E valuation (max +20)
    if pe:
        if 5 < pe < 12:     score += 20
        elif 12 <= pe < 18: score += 12
        elif 18 <= pe < 25: score += 5
        elif pe >= 25:      score -= 5
 
    # Price/Book (max +10)
    if pb:
        if pb < 0.8:   score += 10
        elif pb < 1.2: score += 6
        elif pb < 2.0: score += 3
 
    # Dividend yield (max +15) — SGX retail investors value income
    if div >= 6:   score += 15
    elif div >= 4: score += 11
    elif div >= 2: score += 6
    elif div >= 1: score += 3
 
    # Debt/equity (max +10)
    if de is not None:
        if de < 30:    score += 10
        elif de < 80:  score += 6
        elif de < 150: score += 2
        elif de > 250: score -= 8
 
    # Momentum — steady uptrend preferred (max +10)
    if 2 < mom < 15:     score += 10
    elif 0 < mom <= 2:   score += 5
    elif 15 <= mom < 30: score += 3
    elif mom >= 30:      score -= 3  # overextended
    elif mom < -15:      score -= 8
 
    # Profit margin (max +10)
    if pm:
        if pm > 0.20:   score += 10
        elif pm > 0.10: score += 6
        elif pm > 0.05: score += 3
 
    # Macro sector overlay (+8)
    favoured = macro_context.get('favoured_sectors', [])
    if stock['sector'] in favoured:
        score += 8
 
    return min(100, max(0, score))
 
# ── BATCH FETCHER ─────────────────────────────────────────────────────
def fetch_batch(tickers, batch_size=10):
    """
    Efficient batch fetching — pre-screen using yfinance batch info
    to quickly identify stocks with sufficient market cap before
    fetching full history
    """
    results = []
    failed = 0
    total = len(tickers)
 
    for i, ticker in enumerate(tickers):
        data = get_stock_data(ticker)
        if data:
            results.append(data)
        else:
            failed += 1
 
        # Progress update every 50 stocks
        if (i + 1) % 50 == 0:
            print(f"  Progress: {i+1}/{total} — {len(results)} valid, {failed} no data/delisted")
 
        # Rate limit — be gentle with Yahoo Finance
        time.sleep(0.2)
 
    return results, failed
 
# ── MAIN SCANNER ─────────────────────────────────────────────────────
def run_scanner(macro_context=None):
    """
    Full pipeline:
    1. Load full SGX universe from Symbols_SGX.txt (771 equities)
    2. Fetch fundamentals via yfinance (delisted auto-excluded)
    3. Pre-filter: mkt cap >$50M, volume >100K/day
    4. Score survivors on 6 factors + macro overlay
    5. Return top 30 and top 3
    """
    if macro_context is None:
        macro_context = {'favoured_sectors': ['Financial Services', 'Industrials']}
 
    # Load universe
    all_tickers = load_sgx_universe()
    all_tickers = list(dict.fromkeys(all_tickers))  # deduplicate
    print(f"Scanning {len(all_tickers)} SGX equities from EODData universe...")
    print(f"(Delisted/suspended stocks return no data and are auto-excluded)")
 
    # Fetch data
    results, failed = fetch_batch(all_tickers)
    print(f"\nFetch complete: {len(results)} stocks with data, {failed} excluded (delisted/no data)")
 
    # Pre-filter
    filtered = filter_stocks(results)
    print(f"After pre-filter: {len(filtered)} investable stocks (mkt cap >$50M, vol >100K/day)")
 
    if not filtered:
        print("ERROR: No stocks passed pre-filter — check yfinance connectivity")
        return [], []
 
    # Score all survivors
    for s in filtered:
        s['score'] = score_stock(s, macro_context)
 
    # Rank
    ranked = sorted(filtered, key=lambda x: x['score'], reverse=True)
    top30 = ranked[:30]
    top3 = ranked[:3]
 
    print(f"\nTop 3 SGX Picks this week:")
    for i, s in enumerate(top3):
        div = (s.get('dividend_yield', 0) or 0) * 100
        pe = s.get('pe_ratio')
        pe_str = f"{pe:.1f}" if pe else 'N/A'
        print(f"  {i+1}. {s['ticker']} — {s['name'][:35]}")
        print(f"     Score: {s['score']} | P/E: {pe_str} | Div: {div:.1f}% | 3M: {s['momentum_3m']:.1f}%")
 
    return top30, top3
 
if __name__ == "__main__":
    top30, top3 = run_scanner()
    print(f"\nFull Top 30:")
    for s in top30:
        div = (s.get('dividend_yield', 0) or 0) * 100
        pe = s.get('pe_ratio')
        pe_str = f"{pe:.1f}" if pe else '-'
        print(f"  {s['ticker']:14} {s['name'][:32]:32} Score:{s['score']:3} P/E:{pe_str:6} Div:{div:.1f}%")
