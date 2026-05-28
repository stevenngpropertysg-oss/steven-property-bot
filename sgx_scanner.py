"""
sgx_scanner.py
SGX Stock Scanner — filters ~650 SGX stocks to Top 30, scores and ranks to Top 3
Used by sgx_report.py as Stage 1 of the weekly pipeline
"""

import yfinance as yf
import pandas as pd
import json
import time
from datetime import datetime

# SGX stock universe — major liquid stocks across sectors
SGX_TICKERS = [
    # Banks & Finance
    "D05.SI","O39.SI","U11.SI","H78.SI","S68.SI",
    # REITs
    "C38U.SI","A17U.SI","ME8U.SI","N2IU.SI","J69U.SI","BUOU.SI","K71U.SI","AW9U.SI",
    "T82U.SI","RW0U.SI","AUXU.SI","SK6U.SI","MXNU.SI","HMN.SI","SJ2U.SI",
    # Industrial & Manufacturing
    "D01.SI","C6L.SI","Z74.SI","U96.SI","BS6.SI","BN4.SI","S58.SI","F34.SI",
    "V03.SI","S63.SI","C52.SI","G13.SI","H02.SI","U14.SI","Y92.SI",
    # Technology
    "V2C.SI","BVA.SI","42F.SI","1F3.SI","A7RU.SI",
    # Healthcare
    "Q0F.SI","5WA.SI","580.SI","BMT.SI","40B.SI",
    # Consumer
    "F99.SI","EB5.SI","O32.SI","C2PU.SI","J91U.SI",
    # Shipping & Logistics
    "BS6.SI","Y35.SI","S56.SI","AWX.SI","T55.SI",
    # Property
    "C09.SI","U14.SI","H30.SI","EH7.SI","CCU.SI",
    # Energy & Resources
    "BKV.SI","A55.SI","RQ1.SI","OV8.SI","UD2.SI",
    # Others
    "5TP.SI","558.SI","V77.SI","E28.SI","544.SI","1D0.SI","8YZ.SI","8VC.SI",
]

# Remove duplicates
SGX_TICKERS = list(dict.fromkeys(SGX_TICKERS))

def get_stock_data(ticker):
    """Fetch key fundamentals for a single ticker"""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        hist = stock.history(period="3mo")

        if hist.empty or not info:
            return None

        # Price momentum — 3 month return
        if len(hist) >= 2:
            momentum = (hist['Close'].iloc[-1] - hist['Close'].iloc[0]) / hist['Close'].iloc[0] * 100
        else:
            momentum = 0

        # Average daily volume
        avg_vol = hist['Volume'].mean() if not hist.empty else 0

        data = {
            'ticker': ticker,
            'name': info.get('longName', info.get('shortName', ticker)),
            'price': info.get('currentPrice', info.get('regularMarketPrice', 0)),
            'market_cap': info.get('marketCap', 0),
            'pe_ratio': info.get('trailingPE', None),
            'pb_ratio': info.get('priceToBook', None),
            'dividend_yield': info.get('dividendYield', 0) or 0,
            'debt_to_equity': info.get('debtToEquity', None),
            'revenue': info.get('totalRevenue', 0),
            'profit_margin': info.get('profitMargins', None),
            'avg_volume': avg_vol,
            'momentum_3m': momentum,
            'sector': info.get('sector', 'Unknown'),
            'industry': info.get('industry', 'Unknown'),
        }
        return data
    except Exception as e:
        return None

def score_stock(stock, macro_context):
    """Score stock 0-100 based on fundamentals + macro context"""
    score = 50  # base score

    # Valuation (max +20)
    if stock['pe_ratio'] and 5 < stock['pe_ratio'] < 15:
        score += 20
    elif stock['pe_ratio'] and 15 <= stock['pe_ratio'] < 20:
        score += 10
    elif stock['pe_ratio'] and stock['pe_ratio'] >= 25:
        score -= 10

    # Price to Book (max +10)
    if stock['pb_ratio'] and stock['pb_ratio'] < 1.0:
        score += 10
    elif stock['pb_ratio'] and stock['pb_ratio'] < 1.5:
        score += 5

    # Dividend yield (max +15)
    div = stock['dividend_yield'] * 100 if stock['dividend_yield'] else 0
    if div >= 5:
        score += 15
    elif div >= 3:
        score += 10
    elif div >= 1:
        score += 5

    # Debt (max +10)
    if stock['debt_to_equity'] is not None:
        if stock['debt_to_equity'] < 50:
            score += 10
        elif stock['debt_to_equity'] < 100:
            score += 5
        elif stock['debt_to_equity'] > 200:
            score -= 10

    # Momentum (max +10)
    mom = stock['momentum_3m']
    if 0 < mom < 15:
        score += 10
    elif mom >= 15:
        score += 5
    elif mom < -15:
        score -= 10

    # Profit margin (max +10)
    if stock['profit_margin'] and stock['profit_margin'] > 0.15:
        score += 10
    elif stock['profit_margin'] and stock['profit_margin'] > 0.05:
        score += 5

    # Macro overlay — boost sectors favoured by macro context
    favoured = macro_context.get('favoured_sectors', [])
    if stock['sector'] in favoured:
        score += 10

    return min(100, max(0, score))

def filter_stocks(stocks):
    """Apply baseline filters to eliminate weak candidates"""
    filtered = []
    for s in stocks:
        if not s:
            continue
        # Market cap filter > SGD 50M
        if s['market_cap'] < 50_000_000:
            continue
        # Volume filter > 100K/day
        if s['avg_volume'] < 100_000:
            continue
        # Revenue filter
        if s['revenue'] < 10_000_000:
            continue
        # Exclude negative P/E (loss-making)
        if s['pe_ratio'] and s['pe_ratio'] < 0:
            continue
        filtered.append(s)
    return filtered

def run_scanner(macro_context=None):
    """Main scanner — returns top 30 and top 3"""
    if macro_context is None:
        macro_context = {'favoured_sectors': ['Financial Services', 'Industrials']}

    print(f"Scanning {len(SGX_TICKERS)} SGX stocks...")
    results = []

    for i, ticker in enumerate(SGX_TICKERS):
        data = get_stock_data(ticker)
        if data:
            results.append(data)
        if i % 10 == 0:
            print(f"  Progress: {i}/{len(SGX_TICKERS)}")
        time.sleep(0.3)  # rate limit

    print(f"Raw data: {len(results)} stocks fetched")

    # Filter
    filtered = filter_stocks(results)
    print(f"After filtering: {len(filtered)} stocks")

    # Score
    for s in filtered:
        s['score'] = score_stock(s, macro_context)

    # Sort by score
    ranked = sorted(filtered, key=lambda x: x['score'], reverse=True)
    top30 = ranked[:30]
    top3 = ranked[:3]

    print(f"\nTop 3 SGX Picks:")
    for i, s in enumerate(top3):
        print(f"  {i+1}. {s['ticker']} — {s['name']} (Score: {s['score']})")

    return top30, top3

if __name__ == "__main__":
    top30, top3 = run_scanner()
    print("\nTop 30 candidates:")
    for s in top30:
        print(f"  {s['ticker']:12} {s['name'][:30]:30} Score:{s['score']:3} P/E:{str(s['pe_ratio'])[:5]:6} Div:{s['dividend_yield']*100:.1f}%")
