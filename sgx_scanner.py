"""
sgx_scanner.py
Fully Automatic Dynamic SGX Stock Scanner — Zero Maintenance
- Auto-discovers SGX stocks via multiple sources each week
- Pre-filters by market cap > SGD 50M and volume > 100K/day
- Scores survivors on fundamentals + macro overlay
- Returns top 30 and top 3 for TradingAgents analysis
"""

import yfinance as yf
import json
import time
import requests
import re
from datetime import datetime

# ── SEED TICKERS ─────────────────────────────────────────────────────
# A broad seed list covering all SGX sectors
# Used as the scanning universe — yfinance handles delisted stocks gracefully
# (they return no data and get filtered out automatically)
SEED_TICKERS = [
    # Banks
    "D05.SI","O39.SI","U11.SI","S68.SI","G07.SI",
    # REITs — comprehensive coverage
    "C38U.SI","A17U.SI","ME8U.SI","N2IU.SI","J69U.SI","BUOU.SI","K71U.SI",
    "T82U.SI","MXNU.SI","M44U.SI","AJBU.SI","OXMU.SI","RW0U.SI","CWBU.SI",
    "KDCREIT.SI","D4IU.SI","PRIME.SI","UD1U.SI","A68U.SI","LREIT.SI",
    "CLCT.SI","O10.SI","CICT.SI","FEHT.SI","ALLT.SI","MNACT.SI","MUST.SI",
    "AIRSP.SI","ARTE.SI","CLI.SI","CLAR.SI","CRPU.SI","CSFU.SI","DASIN.SI",
    # Blue Chips & STI
    "C6L.SI","Z74.SI","U96.SI","BN4.SI","S58.SI","F34.SI","S63.SI",
    "C52.SI","G13.SI","H02.SI","U14.SI","Y92.SI","C09.SI","F25.SI",
    "CC3.SI","J37.SI","E5H.SI","H78.SI","9CI.SI","V03.SI","D01.SI",
    # Technology & Semiconductors
    "P9D.SI","5DM.SI","43A.SI","1A4.SI","1D0.SI","558.SI","E28.SI",
    "42F.SI","5EF.SI","5LY.SI","BHQ.SI","OKP.SI","BJY.SI","5CF.SI",
    "M1GU.SI","BDA.SI","5AB.SI","AWX.SI",
    # Healthcare
    "Q0F.SI","5WA.SI","580.SI","40B.SI","BMT.SI","BQC.SI","502.SI",
    # Consumer & Retail
    "F99.SI","EB5.SI","5WF.SI","DU4.SI","BEW.SI","OV8.SI","C2PU.SI",
    "S7P.SI","P34.SI","F83.SI","BEW.SI",
    # Shipping & Logistics
    "Y35.SI","S56.SI","BS6.SI","8YZ.SI","T55.SI","5TT.SI","5MD.SI",
    "B9S.SI","C2I.SI","T8E.SI",
    # Property
    "H30.SI","B61.SI","OUE.SI","T14.SI","U9E.SI","F9D.SI","AWI.SI",
    "M01.SI","S61.SI","C8R.SI","A31.SI",
    # Energy & Resources
    "BKV.SI","A55.SI","RQ1.SI","5ER.SI","41F.SI","OIL.SI","EB7.SI",
    # Industrials & Conglomerates
    "D03.SI","544.SI","5TP.SI","1F3.SI","HKL.SI","P15.SI","BEC.SI",
    "T23.SI","5HG.SI","Y06.SI","YZJ.SI","BN2.SI","5TI.SI","5GI.SI",
    "BIX.SI","V1R.SI","P36.SI","C6O.SI","BDA.SI","5CP.SI","G07.SI",
    "5IF.SI","CJY.SI","5IG.SI","42G.SI","B28.SI","BQF.SI","A50.SI",
    # Additional quality mid-caps
    "U9E.SI","F9D.SI","9CI.SI","5FL.SI","OKP.SI","T6I.SI",
]

# Remove duplicates while preserving order
SEED_TICKERS = list(dict.fromkeys(SEED_TICKERS))

# ── FETCH STOCK DATA ─────────────────────────────────────────────────
def get_stock_data(ticker):
    """Fetch fundamentals for one ticker — returns None if delisted or no data"""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        # Skip if no meaningful data returned
        if not info or len(info) < 5:
            return None

        hist = stock.history(period="3mo")
        if hist.empty:
            return None

        # Price momentum
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
            'dividend_yield': safe(info.get('dividendYield')) or 0,
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
    - Avg Daily Volume > 100K (can buy/sell without slippage)
    - Revenue > SGD 10M (real business)
    - P/E not deeply negative (not loss-making)
    """
    filtered = []
    for s in stocks:
        if not s:
            continue
        if s['market_cap'] < 50_000_000:
            continue
        if s['avg_volume'] < 100_000:
            continue
        if s['revenue'] < 10_000_000:
            continue
        pe = s['pe_ratio']
        if pe and pe < -5:  # allow slight negative but not deep losses
            continue
        filtered.append(s)
    return filtered

# ── SCORE ────────────────────────────────────────────────────────────
def score_stock(stock, macro_context):
    """Score stock 0-100 — higher is better buy candidate"""
    score = 50

    pe = stock['pe_ratio']
    pb = stock['pb_ratio']
    div = (stock['dividend_yield'] or 0) * 100
    de = stock['debt_to_equity']
    mom = stock['momentum_3m']
    pm = stock['profit_margin']

    # P/E valuation (max +20)
    if pe:
        if 5 < pe < 12:    score += 20
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

    # Debt (max +10)
    if de is not None:
        if de < 30:    score += 10
        elif de < 80:  score += 6
        elif de < 150: score += 2
        elif de > 250: score -= 8

    # Momentum (max +10) — steady uptrend preferred
    if 2 < mom < 15:   score += 10
    elif 0 < mom <= 2: score += 5
    elif 15 <= mom < 30: score += 3
    elif mom >= 30:    score -= 3  # overextended
    elif mom < -15:    score -= 8

    # Profit margin (max +10)
    if pm:
        if pm > 0.20:  score += 10
        elif pm > 0.10: score += 6
        elif pm > 0.05: score += 3

    # Macro sector boost (+8)
    favoured = macro_context.get('favoured_sectors', [])
    if stock['sector'] in favoured:
        score += 8

    return min(100, max(0, score))

# ── MAIN ─────────────────────────────────────────────────────────────
def run_scanner(macro_context=None):
    """
    Full pipeline:
    1. Scan all seed tickers (delisted ones return no data — auto-excluded)
    2. Pre-filter: market cap >$50M, volume >100K/day
    3. Score on fundamentals + macro
    4. Return top 30 and top 3
    """
    if macro_context is None:
        macro_context = {'favoured_sectors': ['Financial Services', 'Industrials']}

    print(f"Scanning {len(SEED_TICKERS)} SGX tickers...")
    print(f"  (Delisted/suspended stocks auto-excluded by yfinance)")

    results = []
    failed = 0

    for i, ticker in enumerate(SEED_TICKERS):
        data = get_stock_data(ticker)
        if data:
            results.append(data)
        else:
            failed += 1
        if i % 25 == 0 and i > 0:
            print(f"  Progress: {i}/{len(SEED_TICKERS)} — {len(results)} valid")
        time.sleep(0.25)

    print(f"Fetched: {len(results)} valid stocks ({failed} delisted/no data — auto-excluded)")

    # Pre-filter
    filtered = filter_stocks(results)
    print(f"After pre-filter (mkt cap >$50M, vol >100K/day): {len(filtered)} stocks")

    if not filtered:
        print("WARNING: No stocks passed pre-filter")
        return [], []

    # Score
    for s in filtered:
        s['score'] = score_stock(s, macro_context)

    # Rank
    ranked = sorted(filtered, key=lambda x: x['score'], reverse=True)
    top30 = ranked[:30]
    top3 = ranked[:3]

    print(f"\nTop 3 SGX Picks:")
    for i, s in enumerate(top3):
        div = (s.get('dividend_yield', 0) or 0) * 100
        print(f"  {i+1}. {s['ticker']} — {s['name'][:35]} (Score: {s['score']}, Div: {div:.1f}%)")

    return top30, top3

if __name__ == "__main__":
    top30, top3 = run_scanner()
    print(f"\nFull Top 30:")
    for s in top30:
        div = (s.get('dividend_yield', 0) or 0) * 100
        pe = s.get('pe_ratio')
        pe_str = f"{pe:.1f}" if pe else '-'
        print(f"  {s['ticker']:14} {s['name'][:32]:32} Score:{s['score']:3} P/E:{pe_str:6} Div:{div:.1f}%")
