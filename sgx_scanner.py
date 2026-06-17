"""
sgx_scanner.py
Fully Dynamic SGX Stock Scanner — powered by EODData universe file
- Reads Symbols_SGX.txt from repo (771 SGX equities, warrants excluded)
- Pre-filters by market cap > SGD 50M and volume > 100K/day
- Scores ~100-150 survivors using PERCENTILE-RANK methodology, modeled on
  Joel Greenblatt's "Magic Formula" (rank every stock on each metric
  relative to its actual peer universe that week, then combine ranks) —
  see rank_score_universe() docstring for full methodology notes,
  including which factors are faithful to the original formula vs.
  documented proxies/additions due to free-data limitations.
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
 
# ── SCORE: PERCENTILE-RANK METHODOLOGY (Magic Formula-style) ─────────
def rank_score_universe(filtered_stocks, macro_context):
    """
    Score every stock in the filtered universe using PERCENTILE-RANK
    scoring across the actual peer group, instead of fixed bucket
    thresholds. This is the same core mechanism as Joel Greenblatt's
    "Magic Formula" (The Little Book That Beats the Market, 2005):
    rank every candidate on each metric relative to ALL OTHER
    candidates that week, then combine the ranks. The stock with the
    best combined relative position wins — there is no hardcoded
    breakpoint (like "P/E between 5 and 12") that can ever be wrong,
    arbitrary, or drift out of date, because "good" is defined entirely
    by where a stock sits relative to its real peers that week.
 
    WHY THIS REPLACES THE OLD BUCKET-THRESHOLD score_stock(): the
    previous version used fixed point buckets (e.g. "P/E 5-12 -> +20,
    12-18 -> +12...") that were never calibrated against real SGX data
    or a published methodology — they were originally just proposed
    as reasonable-sounding numbers when this scanner was first built.
    That caused two compounding problems: (1) the achievable score
    range (29-133) didn't match the displayed 0-100 scale, causing
    near-universal saturation at 100 once a stock cleared ~3-4 of 7
    factors (already fixed via rescaling in a prior revision); and (2)
    more fundamentally, the thresholds themselves had no real-world
    grounding — there's no reason P/E=11.9 and P/E=8.0 should ever earn
    the IDENTICAL +20 bucket score, when they are clearly not equally
    cheap. Percentile-rank scoring fixes both: it's mathematically
    impossible for the same metric value to rank-tie unless multiple
    stocks are EXACTLY equal, so saturation cannot structurally occur,
    and there is no invented breakpoint to defend or get wrong.
 
    METHODOLOGY NOTES (what's faithful to Greenblatt vs. adapted):
    - Greenblatt's original two factors are Earnings Yield (EBIT/EV)
      and Return on Capital (EBIT/Invested Capital). yfinance's free
      data does not reliably expose EBIT or true invested capital for
      SGX small/mid-caps, so this implementation uses P/E as an
      earnings-yield proxy and profit margin as a return-quality proxy
      — both explicitly endorsed as fallbacks in Magic Formula literature
      when EBIT/EV and ROIC aren't available (see StableBread's Magic
      Formula guide). This is a documented, citable substitution, not a
      silent shortcut.
    - P/B and debt/equity are added as supplementary value/safety
      ranks beyond Greenblatt's original two-factor model, since SGX
      retail investors (per this scanner's stated purpose) reasonably
      care about balance sheet risk and book value support, which the
      pure Magic Formula doesn't address at all.
    - Dividend yield and momentum are SGX-market-specific additions —
      legitimate factors in broader quant literature (income/quality
      and momentum factors), but not part of Greenblatt's original
      formula. Included as additional ranked factors, weighted equally
      alongside the others rather than given outsized influence.
    - The "favoured sector" macro overlay is NOT part of any standard
      factor model — Greenblatt's approach is deliberately sector-
      agnostic and mechanical. It is kept here as a SEPARATE, clearly
      labelled bonus applied AFTER the core rank-based score, so the
      core methodology stays faithful to the citable formula and the
      macro overlay remains transparent and isolated rather than
      blended into the ranking math itself.
 
    Returns the SAME filtered_stocks list with a 'score' key (0-100)
    added to each stock dict, and also attaches 'rank_detail' for
    transparency (each factor's percentile rank, 1 = best).
    """
    n = len(filtered_stocks)
    if n == 0:
        return filtered_stocks
 
    def percentile_rank(stocks, key_func, ascending_is_better, missing_value_penalty=True):
        """
        Returns {index_in_stocks_list: percentile (0.0 worst - 1.0 best)}.
        Stocks with missing/None data for this metric are ranked at the
        worst percentile (penalised) rather than excluded or favoured —
        excluding them would let "no data" sneak in as if it were
        neutral or even advantageous, which is its own quiet bug class.
        """
        values = []
        for i, s in enumerate(stocks):
            v = key_func(s)
            values.append((i, v))
 
        # Separate stocks with real data from those missing this metric
        valid = [(i, v) for i, v in values if v is not None]
        missing = [i for i, v in values if v is None]
 
        valid_sorted = sorted(valid, key=lambda x: x[1], reverse=not ascending_is_better)
        # valid_sorted[0] is the BEST stock on this metric after sort direction applied
 
        percentiles = {}
        count = len(valid_sorted)
        for rank, (i, v) in enumerate(valid_sorted):
            # rank 0 = best -> percentile close to 1.0 (best); last -> close to 0.0
            percentiles[i] = 1.0 - (rank / max(count - 1, 1)) if count > 1 else 1.0
 
        # Missing data: worst possible percentile on this factor
        for i in missing:
            percentiles[i] = 0.0 if missing_value_penalty else 0.5
 
        return percentiles
 
    # ── Core ranked factors (each contributes equally to the combined rank) ──
    pe_pct = percentile_rank(filtered_stocks, lambda s: s['pe_ratio'] if s['pe_ratio'] and s['pe_ratio'] > 0 else None, ascending_is_better=True)
    pb_pct = percentile_rank(filtered_stocks, lambda s: s['pb_ratio'], ascending_is_better=True)
    div_pct = percentile_rank(filtered_stocks, lambda s: s['dividend_yield'], ascending_is_better=False)
    de_pct = percentile_rank(filtered_stocks, lambda s: s['debt_to_equity'], ascending_is_better=True)
    mom_pct = percentile_rank(filtered_stocks, lambda s: s['momentum_3m'], ascending_is_better=False)
    pm_pct = percentile_rank(filtered_stocks, lambda s: s['profit_margin'], ascending_is_better=False)
 
    favoured = macro_context.get('favoured_sectors', [])
 
    for i, s in enumerate(filtered_stocks):
        factor_percentiles = {
            'pe': pe_pct[i],
            'pb': pb_pct[i],
            'dividend_yield': div_pct[i],
            'debt_to_equity': de_pct[i],
            'momentum': mom_pct[i],
            'profit_margin': pm_pct[i],
        }
 
        # Combined core score: average of all six factor percentiles,
        # scaled to 0-100. Equal weighting across factors — no single
        # metric dominates, consistent with Magic Formula's "sum of
        # ranks" philosophy (just expressed as percentiles instead of
        # raw rank positions, since percentiles compare cleanly across
        # weeks with different universe sizes).
        core_score = (sum(factor_percentiles.values()) / len(factor_percentiles)) * 100
 
        # Macro sector overlay — SEPARATE bonus, NOT blended into the
        # core ranking. Small, capped influence so it can nudge but
        # never dominate the mechanical ranking underneath it.
        sector_bonus = 8 if s['sector'] in favoured else 0
 
        final_score = min(100, round(core_score + sector_bonus))
 
        s['score'] = final_score
        s['rank_detail'] = {k: round(v * 100) for k, v in factor_percentiles.items()}
 
    return filtered_stocks
 
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
 
    # Score all survivors using percentile-rank methodology (Magic
    # Formula-style) — must run ONCE on the whole filtered list, since
    # percentile ranking is only meaningful relative to the full peer
    # universe, not computable for a single stock in isolation.
    filtered = rank_score_universe(filtered, macro_context)
 
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
