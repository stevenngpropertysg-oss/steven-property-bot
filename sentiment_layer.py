"""
sentiment_layer.py
==================
Track 3 Enhancement — Sentiment Analysis Layer
Runs BEFORE TradingAgents deep dive to filter out stocks with bad news.

HOW IT WORKS:
1. Takes Top 3 stocks from sgx_scanner.py
2. For each stock, searches recent news (last 7 days)
3. Scores sentiment: -2 (Very Bearish) to +2 (Very Bullish)
4. Flags RED alerts: analyst downgrades, profit warnings, scandals
5. Only passes stocks with score >= 0 to TradingAgents
6. Adds sentiment section to weekly HTML report

INTEGRATION:
- Add import at top of sgx_weekly_report.py
- Call analyze_sentiment(ticker, company_name) for each Top 3 stock
- Use result to filter before TradingAgents call
- Include sentiment_html in weekly report

COST: ~USD 0.20 per stock = ~USD 0.60 per week total
"""

import anthropic
import json
import os
import requests
from datetime import datetime, timedelta, timezone

# ── CONFIG ──────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SGT = timezone(timedelta(hours=8))

# Sentiment score thresholds
SCORE_LABELS = {
    2:  ("🟢 VERY BULLISH",  "#1a7a1a", "Strong positive news momentum"),
    1:  ("🟢 BULLISH",       "#2e7d32", "Positive news momentum"),
    0:  ("🟡 NEUTRAL",       "#b8860b", "Mixed or no significant news"),
   -1:  ("🔴 BEARISH",       "#c62828", "Negative news — caution advised"),
   -2:  ("🔴 VERY BEARISH",  "#7b1515", "Strong negative news — SKIP"),
}

# Red flag keywords that trigger automatic -2 override
RED_FLAG_KEYWORDS = [
    "profit warning", "earnings miss", "revenue decline", "loss", "write-down",
    "write-off", "lawsuit", "fraud", "investigation", "regulatory action",
    "MAS action", "SGX query", "trading suspension", "delisting",
    "downgrade", "cut to sell", "cut to hold", "target price cut",
    "CEO resigned", "CFO resigned", "management change", "going concern",
    "debt default", "bond default", "liquidation", "judicial management"
]

# ── SENTIMENT ANALYSIS ──────────────────────────────────────
def analyze_sentiment(ticker: str, company_name: str, client=None) -> dict:
    """
    Analyze sentiment for a single SGX stock.
    
    Args:
        ticker: SGX ticker e.g. "5CF.SI" or "41O.SI"
        company_name: Company name e.g. "OKP Holdings"
        client: Anthropic client (optional, creates one if not provided)
    
    Returns:
        {
            "ticker": "5CF.SI",
            "company": "OKP Holdings",
            "score": 1,          # -2 to +2
            "label": "BULLISH",
            "color": "#2e7d32",
            "headlines": [...],  # list of key headlines
            "red_flags": [...],  # list of red flag phrases found
            "summary": "...",    # 2-sentence summary
            "pass_filter": True, # True if score >= 0
            "analyst_target": "SGD X.XX",  # if found
            "analyst_action": "BUY/HOLD/SELL"  # latest analyst call
        }
    """
    if client is None:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    now_sgt = datetime.now(SGT)
    week_ago = (now_sgt - timedelta(days=7)).strftime("%d %b %Y")
    today = now_sgt.strftime("%d %b %Y")
    
    # Clean ticker for search (remove .SI suffix)
    clean_ticker = ticker.replace(".SI", "")

    prompt = f"""You are a Singapore equity research analyst. 
Today is {today}. Analyze the sentiment for {company_name} (SGX: {clean_ticker}) 
based on news and events from the past 7 days ({week_ago} to {today}).

Search for and analyze:
1. Recent earnings, results, or financial announcements
2. Analyst upgrades/downgrades and target price changes
3. Corporate actions (dividends, rights issues, acquisitions, disposals)
4. Management changes or governance issues
5. Regulatory actions or SGX queries
6. Macroeconomic factors specific to this stock's sector
7. Trading volume anomalies or unusual price movements

Return a JSON object with EXACTLY this structure (no other text):
{{
    "score": <integer from -2 to 2>,
    "score_rationale": "<one sentence explaining the score>",
    "headlines": [
        "<headline 1 - most important news>",
        "<headline 2>",
        "<headline 3 if available>"
    ],
    "red_flags": [
        "<red flag phrase if any, e.g. 'analyst downgrade to HOLD'>",
    ],
    "positive_catalysts": [
        "<positive catalyst if any>"
    ],
    "summary": "<2 sentences: what happened this week and what it means for the stock>",
    "analyst_latest": {{
        "action": "<BUY/HOLD/SELL/OUTPERFORM/NEUTRAL/UNDERPERFORM or NONE>",
        "target_price": "<SGD X.XX or NONE>",
        "broker": "<broker name or NONE>"
    }},
    "sector_tailwind": "<POSITIVE/NEUTRAL/NEGATIVE - sector macro backdrop>",
    "data_confidence": "<HIGH/MEDIUM/LOW - how much recent data was available>"
}}

Score guide:
+2 = Strong positive catalysts (earnings beat, major contract win, analyst upgrade with big target raise)
+1 = Mild positive (inline results, small contract, maintained BUY rating)
0  = Neutral (no significant news, or mixed signals)
-1 = Mild negative (slight earnings miss, analyst downgrade to HOLD, minor concern)
-2 = Strong negative (profit warning, fraud, major lawsuit, suspended trading, cut to SELL)

Be conservative — when in doubt score 0, not +1."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search"
            }],
            messages=[{"role": "user", "content": prompt}]
        )

        # Extract text response
        result_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                result_text += block.text

        # Parse JSON
        result_text = result_text.strip()
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()

        data = json.loads(result_text)

        # Validate and clamp score
        score = max(-2, min(2, int(data.get("score", 0))))

        # Check for red flag keywords in headlines and summary
        all_text = " ".join(data.get("headlines", []) + 
                           data.get("red_flags", []) + 
                           [data.get("summary", "")]).lower()
        
        detected_red_flags = [kw for kw in RED_FLAG_KEYWORDS if kw in all_text]
        if detected_red_flags and score > -1:
            score = -1  # Auto-downgrade if red flags detected

        label, color, _ = SCORE_LABELS[score]
        analyst = data.get("analyst_latest", {})

        return {
            "ticker": ticker,
            "company": company_name,
            "score": score,
            "label": label,
            "color": color,
            "headlines": data.get("headlines", [])[:3],
            "red_flags": data.get("red_flags", []) + detected_red_flags,
            "positive_catalysts": data.get("positive_catalysts", []),
            "summary": data.get("summary", "No recent news found."),
            "analyst_action": analyst.get("action", "NONE"),
            "analyst_target": analyst.get("target_price", "NONE"),
            "analyst_broker": analyst.get("broker", "NONE"),
            "sector_tailwind": data.get("sector_tailwind", "NEUTRAL"),
            "data_confidence": data.get("data_confidence", "LOW"),
            "pass_filter": score >= 0,
            "score_rationale": data.get("score_rationale", "")
        }

    except Exception as e:
        # On error, return neutral — don't block the stock
        return {
            "ticker": ticker,
            "company": company_name,
            "score": 0,
            "label": "🟡 NEUTRAL",
            "color": "#b8860b",
            "headlines": [],
            "red_flags": [],
            "positive_catalysts": [],
            "summary": f"Sentiment analysis unavailable: {str(e)[:100]}",
            "analyst_action": "NONE",
            "analyst_target": "NONE",
            "analyst_broker": "NONE",
            "sector_tailwind": "NEUTRAL",
            "data_confidence": "LOW",
            "pass_filter": True,  # Allow through on error
            "score_rationale": "Error in analysis"
        }


# ── BATCH ANALYSIS ───────────────────────────────────────────
def analyze_top3_sentiment(stocks: list) -> list:
    """
    Analyze sentiment for list of stocks.
    
    Args:
        stocks: list of dicts with keys "ticker" and "company"
                e.g. [{"ticker": "5CF.SI", "company": "OKP Holdings"}, ...]
    
    Returns:
        list of sentiment results, sorted by score descending
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    results = []
    
    for stock in stocks:
        print(f"  Analyzing sentiment: {stock['company']} ({stock['ticker']})...")
        result = analyze_sentiment(stock["ticker"], stock["company"], client)
        results.append(result)
        
        status = "✅ PASS" if result["pass_filter"] else "❌ FILTERED"
        print(f"    Score: {result['score']} | {result['label']} | {status}")
        if result["red_flags"]:
            print(f"    ⚠️  Red flags: {', '.join(result['red_flags'][:2])}")

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ── HTML GENERATION ──────────────────────────────────────────
def generate_sentiment_html(results: list) -> str:
    """Generate HTML section for sentiment analysis to embed in weekly report."""
    
    passed = [r for r in results if r["pass_filter"]]
    filtered = [r for r in results if not r["pass_filter"]]

    html = """
    <div style="background:#1a1a2e; border-radius:12px; padding:20px; margin:20px 0;">
        <h2 style="color:#58a6ff; font-size:18px; margin:0 0 16px 0; 
                   border-bottom:2px solid #2e5a9c; padding-bottom:8px;">
            📰 SENTIMENT ANALYSIS — PRE-FILTER
        </h2>
    """

    for r in results:
        border_color = r["color"]
        status_badge = (
            '<span style="background:#1a7a1a; color:#90EE90; padding:2px 10px; '
            'border-radius:10px; font-size:11px; font-weight:bold;">✅ PASS</span>'
            if r["pass_filter"] else
            '<span style="background:#7b1515; color:#FFB3B3; padding:2px 10px; '
            'border-radius:10px; font-size:11px; font-weight:bold;">❌ FILTERED</span>'
        )

        html += f"""
        <div style="background:#0d1117; border-left:4px solid {border_color}; 
                    border-radius:8px; padding:14px; margin-bottom:12px;">
            <div style="display:flex; justify-content:space-between; 
                        align-items:center; margin-bottom:8px;">
                <div>
                    <span style="font-weight:bold; color:#e6edf3; font-size:15px;">
                        {r['company']}
                    </span>
                    <span style="color:#8b949e; font-size:12px; margin-left:8px;">
                        {r['ticker']}
                    </span>
                </div>
                <div style="display:flex; gap:8px; align-items:center;">
                    <span style="color:{border_color}; font-weight:bold; font-size:14px;">
                        {r['label']}
                    </span>
                    {status_badge}
                </div>
            </div>
        """

        # Score bar
        score_pct = (r["score"] + 2) / 4 * 100  # Convert -2..+2 to 0..100%
        html += f"""
            <div style="background:#21262d; border-radius:4px; height:6px; 
                        margin-bottom:10px; overflow:hidden;">
                <div style="background:{border_color}; height:100%; 
                            width:{score_pct}%; border-radius:4px;"></div>
            </div>
        """

        # Summary
        html += f"""
            <p style="color:#8b949e; font-size:12px; margin:0 0 8px 0; 
                      font-style:italic;">
                {r['summary']}
            </p>
        """

        # Headlines
        if r["headlines"]:
            html += '<div style="margin-bottom:8px;">'
            for h in r["headlines"][:3]:
                html += f"""
                <div style="color:#cadcfc; font-size:12px; padding:3px 0; 
                            border-bottom:1px solid #21262d;">
                    📌 {h}
                </div>"""
            html += "</div>"

        # Analyst call
        if r["analyst_action"] not in ["NONE", ""]:
            analyst_color = (
                "#3fb950" if r["analyst_action"] in ["BUY", "OUTPERFORM", "ADD"] 
                else "#f85149" if r["analyst_action"] in ["SELL", "UNDERPERFORM", "REDUCE"]
                else "#d29922"
            )
            html += f"""
            <div style="display:flex; gap:12px; font-size:12px; margin-top:6px;">
                <span style="color:{analyst_color}; font-weight:bold;">
                    📊 {r['analyst_action']}
                </span>"""
            if r["analyst_target"] != "NONE":
                html += f"""
                <span style="color:#8b949e;">
                    Target: <span style="color:#cadcfc;">{r['analyst_target']}</span>
                </span>"""
            if r["analyst_broker"] != "NONE":
                html += f"""
                <span style="color:#8b949e;">
                    ({r['analyst_broker']})
                </span>"""
            html += "</div>"

        # Red flags
        if r["red_flags"]:
            html += '<div style="margin-top:8px;">'
            for flag in r["red_flags"][:3]:
                html += f"""
                <span style="background:#3a1515; color:#ff9999; font-size:11px; 
                             padding:2px 8px; border-radius:10px; margin-right:4px;">
                    ⚠️ {flag}
                </span>"""
            html += "</div>"

        html += "</div>"  # end stock card

    # Summary bar
    html += f"""
        <div style="background:#161b22; border-radius:8px; padding:12px; 
                    margin-top:8px; display:flex; gap:20px;">
            <div style="color:#3fb950; font-size:13px;">
                ✅ Passed filter: <strong>{len(passed)}</strong>
            </div>
            <div style="color:#f85149; font-size:13px;">
                ❌ Filtered out: <strong>{len(filtered)}</strong>
            </div>
            <div style="color:#8b949e; font-size:12px; margin-left:auto;">
                Stocks with score ≥ 0 proceed to TradingAgents analysis
            </div>
        </div>
    </div>
    """

    return html


# ── PORTFOLIO REVIEW ─────────────────────────────────────────
def analyze_portfolio_sentiment(holdings: list) -> str:
    """
    Check current holdings against sentiment signals.
    Alerts if any holding shows strong negative sentiment.
    
    Args:
        holdings: list of dicts e.g. 
            [{"ticker": "5CF.SI", "company": "OKP Holdings", 
              "shares": 15000, "avg_price": 0.822}]
    
    Returns:
        HTML string for portfolio review section
    """
    if not holdings:
        return ""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    results = []

    for h in holdings:
        result = analyze_sentiment(h["ticker"], h["company"], client)
        result["shares"] = h.get("shares", 0)
        result["avg_price"] = h.get("avg_price", 0)
        results.append(result)

    html = """
    <div style="background:#1a1a2e; border-radius:12px; padding:20px; margin:20px 0;">
        <h2 style="color:#d29922; font-size:18px; margin:0 0 16px 0;
                   border-bottom:2px solid #b8860b; padding-bottom:8px;">
            💼 YOUR PORTFOLIO REVIEW
        </h2>
    """

    for r in results:
        alert = r["score"] <= -1
        border = "#f85149" if alert else "#30363d"
        alert_badge = (
            '<span style="background:#7b1515; color:#ff9999; padding:2px 8px; '
            'border-radius:10px; font-size:11px;">⚠️ REVIEW POSITION</span>'
            if alert else ""
        )

        html += f"""
        <div style="background:#0d1117; border:1px solid {border}; 
                    border-radius:8px; padding:12px; margin-bottom:10px;">
            <div style="display:flex; justify-content:space-between;">
                <div>
                    <span style="font-weight:bold; color:#e6edf3;">{r['company']}</span>
                    <span style="color:#8b949e; font-size:12px;"> {r['ticker']}</span>
                    <span style="color:#8b949e; font-size:12px; margin-left:8px;">
                        {r.get('shares', 0):,} shares @ SGD {r.get('avg_price', 0):.3f}
                    </span>
                </div>
                <div style="display:flex; gap:8px; align-items:center;">
                    <span style="color:{r['color']}; font-size:13px; font-weight:bold;">
                        {r['label']}
                    </span>
                    {alert_badge}
                </div>
            </div>
            <p style="color:#8b949e; font-size:12px; margin:8px 0 0 0;">
                {r['summary']}
            </p>
        </div>
        """

    html += "</div>"
    return html


# ── INTEGRATION HELPER ───────────────────────────────────────
def run_sentiment_pipeline(top3_stocks: list, portfolio_holdings: list = None) -> dict:
    """
    Main function to call from sgx_weekly_report.py
    
    Args:
        top3_stocks: [{"ticker": "5CF.SI", "company": "OKP Holdings"}, ...]
        portfolio_holdings: [{"ticker": "5CF.SI", "company": "OKP", 
                              "shares": 15000, "avg_price": 0.822}, ...]
    
    Returns:
        {
            "results": [...],          # all sentiment results
            "passed": [...],           # stocks that pass filter (score >= 0)
            "filtered": [...],         # stocks filtered out
            "sentiment_html": "...",   # HTML for weekly report
            "portfolio_html": "...",   # HTML for portfolio review
        }
    """
    print("\n📰 Running sentiment analysis...")
    results = analyze_top3_sentiment(top3_stocks)
    
    passed = [r for r in results if r["pass_filter"]]
    filtered = [r for r in results if not r["pass_filter"]]
    
    print(f"\n  ✅ Passed: {len(passed)} stocks")
    if filtered:
        print(f"  ❌ Filtered: {len(filtered)} stocks — {[r['ticker'] for r in filtered]}")

    sentiment_html = generate_sentiment_html(results)
    
    portfolio_html = ""
    if portfolio_holdings:
        print("\n💼 Analyzing portfolio holdings...")
        portfolio_html = analyze_portfolio_sentiment(portfolio_holdings)

    return {
        "results": results,
        "passed": passed,
        "filtered": filtered,
        "sentiment_html": sentiment_html,
        "portfolio_html": portfolio_html
    }


# ── DIRECT TEST ──────────────────────────────────────────────
if __name__ == "__main__":
    """Test the sentiment layer directly"""
    
    # Test with your current holdings
    test_stocks = [
        {"ticker": "5CF.SI", "company": "OKP Holdings"},
        {"ticker": "41O.SI", "company": "LHN Limited"},
        {"ticker": "AJBU.SI", "company": "Keppel DC REIT"},
    ]
    
    test_portfolio = [
        {"ticker": "5CF.SI", "company": "OKP Holdings", 
         "shares": 15000, "avg_price": 0.822},
    ]
    
    output = run_sentiment_pipeline(test_stocks, test_portfolio)
    
    print("\n" + "="*60)
    print("SENTIMENT RESULTS:")
    print("="*60)
    for r in output["results"]:
        print(f"\n{r['company']} ({r['ticker']})")
        print(f"  Score: {r['score']} | {r['label']}")
        print(f"  Pass filter: {r['pass_filter']}")
        print(f"  Summary: {r['summary'][:100]}...")
        if r["analyst_action"] != "NONE":
            print(f"  Analyst: {r['analyst_action']} @ {r['analyst_target']}")
