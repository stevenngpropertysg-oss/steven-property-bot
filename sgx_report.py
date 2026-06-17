"""
sgx_report.py
Weekly SGX Intelligence Report Generator
Runs every Sunday 8pm SGT via GitHub Actions
Pushes HTML report to Gist → stevenngwealth.sg members area
"""
 
import anthropic
import requests
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from sgx_scanner import run_scanner
from sentiment_layer import run_sentiment_pipeline
 
# ── CONFIG ──────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GIST_TOKEN = os.environ.get("GIST_TOKEN")
GIST_ID = os.environ.get("GIST_ID", "cddead61d5c68fca48ae7fe67ff2a2a4")
MODEL = "claude-sonnet-4-5"
 
# SGT timezone
SGT = timezone(timedelta(hours=8))
NOW = datetime.now(SGT)
WEEK_STR = NOW.strftime("%d %B %Y")
GENERATED = NOW.strftime("%d %b %Y, %I:%M %p SGT")
 
# ── YOUR PORTFOLIO (update weekly) ──────────────────────────────────
MY_PORTFOLIO = [
    {"ticker": "5CF.SI",  "company": "OKP Holdings",  "shares": 15000, "avg_price": 0.822},
    # Add/remove holdings as they change:
    # {"ticker": "UIBU.SI", "company": "UIBREIT", "shares": 15000, "avg_price": 0.8166},
]
 
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
 
 
# ── SHARED HELPER: properly-looped agentic web search ────────────────
def run_agentic_search(prompt, max_tokens=1500, max_turns=6):
    """
    THE BUG THIS FIXES: the previous code called
    client.messages.create(..., tools=[web_search]) exactly once and read
    whatever text blocks were in that single response. When Claude decides
    to use the search tool, that API turn ends with stop_reason="tool_use"
    — Claude is mid-thought, waiting to read the search result before it
    can write its actual answer. The old code never made a follow-up call,
    so on any prompt complex enough to need a search before answering (all
    three call sites in this file), the saved "answer" was just Claude's
    interrupted reasoning ("Let me search for...", "Based on my search, I
    now have sufficient information... Let me compile...") with no actual
    bull case / bear case / verdict ever produced. That is exactly what
    showed up broken on the live OKP report.
 
    THE FIX: loop on stop_reason, feeding the full assistant turn back into
    the message history (the server-side web_search tool's result is
    already attached to response.content by Anthropic — no manual fetch
    needed) and explicitly prompting the model to continue, until it
    reaches stop_reason == "end_turn" or we hit a safety cap on turns.
    """
    messages = [{"role": "user", "content": prompt}]
    response = None
 
    for _ in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages,
        )
 
        messages.append({"role": "assistant", "content": response.content})
 
        if response.stop_reason != "tool_use":
            return "".join(
                block.text for block in response.content if hasattr(block, "text")
            )
 
        messages.append({
            "role": "user",
            "content": "Please continue and provide your complete final answer now.",
        })
 
    # Hit max_turns without finishing — return what we have, clearly flagged,
    # rather than silently returning a truncated answer as if it were complete.
    partial = "".join(
        block.text for block in response.content if hasattr(block, "text")
    ) if response else ""
    return partial + "\n\n[⚠ Response may be incomplete — hit max search turns]"
 
 
# ── STEP 1: MACRO SCAN ───────────────────────────────────────────────
def get_macro_analysis():
    print("Step 1: Macro scan...")
    prompt = """You are a senior Singapore-based macro analyst. Today is {date}.
 
Analyse the current global and Singapore macro environment and provide:
 
1. GLOBAL MACRO (5 key signals):
   - US Federal Reserve rate stance and next expected move
   - USD/SGD current level and trend direction
   - Brent crude oil price and trend
   - China PMI and economic momentum
   - US equity market sentiment (S&P 500 trend)
 
2. SINGAPORE MACRO (3 key signals):
   - MAS monetary policy stance
   - STI (Straits Times Index) trend and key level
   - Singapore GDP/economic outlook
 
3. SECTOR ROTATION (which SGX sectors to favour this week and why):
   - Top 2 favoured sectors with brief reason
   - Top 1 sector to avoid with brief reason
 
4. MACRO VERDICT FOR SGX INVESTORS:
   - Overall market environment: RISK-ON / RISK-OFF / NEUTRAL
   - One key risk to watch this week
   - One key opportunity this week
 
Keep each point concise — 1-2 sentences max. Be specific with numbers where possible.
Once your search is complete, you MUST output your final answer as JSON with keys:
global_signals, singapore_signals, sector_rotation, verdict, favoured_sectors (list of sector names).
Output ONLY the JSON in your final message, no other text.""".format(date=WEEK_STR)
 
    text = run_agentic_search(prompt, max_tokens=1500)
 
    try:
        import re
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            macro_data = json.loads(json_match.group())
        else:
            macro_data = {"raw": text, "favoured_sectors": ["Financial Services", "Industrials"]}
    except Exception:
        macro_data = {"raw": text, "favoured_sectors": ["Financial Services", "Industrials"]}
 
    print("  Macro scan complete.")
    return macro_data
 
 
# ── STEP 2: MICRO SCAN ───────────────────────────────────────────────
def get_micro_analysis():
    print("Step 2: Micro scan...")
    prompt = """You are a Singapore equity analyst monitoring SGX corporate news. Today is {date}.
 
Search for and summarise the most important Singapore stock market micro events from the past 7 days:
 
1. EARNINGS & RESULTS (any SGX companies that reported earnings this week)
2. DIVIDENDS (any notable ex-dividend dates or dividend announcements)
3. CORPORATE ACTIONS (M&A, rights issues, share buybacks, insider trading)
4. SGX REGULATORY FILINGS (any significant announcements)
5. SECTOR NEWS (key developments affecting Singapore-listed sectors)
 
For each item provide:
- Company name and ticker
- What happened
- Impact: POSITIVE / NEGATIVE / NEUTRAL for investors
 
List top 5 most market-moving micro events only. Be specific and factual.
Once your search is complete, write out your final summary as plain text in your
final message — do not stop after searching, you must produce the actual summary.""".format(date=WEEK_STR)
 
    text = run_agentic_search(prompt, max_tokens=1500)
 
    print("  Micro scan complete.")
    return text
 
 
# ── STEP 2B: WEEKLY LINKEDIN POST (Insurance/Wealth angle) ───────────
def get_linkedin_post(macro_data, micro_text):
    print("Step 2b: Generating weekly LinkedIn post...")
 
    verdict = macro_data.get('verdict', {})
    market_env = verdict.get('overall', 'NEUTRAL') if isinstance(verdict, dict) else 'NEUTRAL'
 
    # Pull the already-researched macro signals (global + Singapore) so the
    # post can ground itself in real, current figures from this week's scan
    # (e.g. MAS policy moves, STI level, GDP outlook) instead of needing a
    # fresh, separate search for its own stat.
    global_signals = macro_data.get('global_signals', {})
    sg_signals = macro_data.get('singapore_signals', {})
    global_signals_str = json.dumps(global_signals) if isinstance(global_signals, dict) else str(global_signals)[:800]
    sg_signals_str = json.dumps(sg_signals) if isinstance(sg_signals, dict) else str(sg_signals)[:800]
 
    prompt = """You are writing a weekly LinkedIn post for Steven Ng, a Singapore-based
financial education content creator who is licensed in RES5, M9, and HI (insurance/wealth
planning representative exams).
 
This week's macro research (already gathered — use this as your primary source, no need
to search again unless you want to verify or add one extra figure):
 
GLOBAL SIGNALS: {global_signals}
 
SINGAPORE SIGNALS: {sg_signals}
 
Market environment: {market_env}
Micro/corporate news summary: {micro}
 
Write a LinkedIn post that:
- Picks ONE concrete figure from the Singapore signals above (e.g. MAS policy stance,
  STI level/trend, GDP growth) and uses it as the opening stat/hook — prefer Singapore
  signals over global ones since the audience is Singaporean
- Connects that figure to an insurance/wealth/CPF/retirement planning insight — e.g. if
  MAS is tightening to fight inflation, what does that mean for someone's savings or
  protection planning; if STI is near highs, what does that mean for someone sitting in cash
- Targets Singaporeans thinking about insurance, CPF optimisation, or retirement planning
- Structure: 1 stat, 1 insight, 1 call to action
- Honest, no hype, numbers-first tone — matches Steven's site positioning as "data-driven,
  no jargon, no hidden agenda"
- Do NOT give specific personalised financial advice or recommend specific products —
  Steven is licensed but this is general education content, not a solicitation
- End with: "Follow me for weekly Singapore wealth & insurance insights."
- Add 3-4 relevant hashtags (e.g. #SingaporeInsurance #CPF #WealthPlanning #FinancialEducation)
- Max 150 words
- Use line breaks between sections for readability
 
Write out the COMPLETE final post in your final message. The post itself is the
deliverable — output ONLY the post text, no preamble, no markdown formatting characters.
Only use the web_search tool if you need to verify or supplement a figure — the macro
data above should usually be enough on its own.""".format(
        market_env=market_env,
        micro=micro_text[:500],
        global_signals=global_signals_str,
        sg_signals=sg_signals_str
    )
 
    text = run_agentic_search(prompt, max_tokens=800, max_turns=4)
 
    print("  LinkedIn post generated.")
    return text.strip()
 
 
# ── STEP 3: SGX SCANNER ──────────────────────────────────────────────
def run_sgx_scanner(macro_data):
    print("Step 3: Running SGX scanner...")
    favoured = macro_data.get('favoured_sectors', ['Financial Services', 'Industrials'])
    macro_context = {'favoured_sectors': favoured}
    top30, top3 = run_scanner(macro_context)
    print(f"  Scanner complete. Top 3: {[s['ticker'] for s in top3]}")
    return top30, top3
 
 
# ── STEP 3B: SENTIMENT FILTER ────────────────────────────────────────
def run_sentiment_filter(top3):
    print("Step 3b: Running sentiment analysis...")
    top3_for_sentiment = [
        {"ticker": s["ticker"], "company": s["name"]} for s in top3
    ]
    sentiment_output = run_sentiment_pipeline(top3_for_sentiment, MY_PORTFOLIO)
 
    passed_stocks = sentiment_output["passed"]
    stocks_to_analyse = [
        s for s in top3
        if any(p["ticker"] == s["ticker"] for p in passed_stocks)
    ] or top3  # fallback to all top3 if all filtered
 
    print(f"  Sentiment filter: {len(stocks_to_analyse)}/{len(top3)} stocks passed")
    if len(stocks_to_analyse) < len(top3):
        filtered = [
            s["ticker"] for s in top3
            if not any(p["ticker"] == s["ticker"] for p in passed_stocks)
        ]
        print(f"  Filtered out: {filtered}")
 
    return stocks_to_analyse, sentiment_output
 
 
# ── STEP 4: TRADINGAGENTS ANALYSIS ──────────────────────────────────
def analyse_top3(top3, macro_summary):
    print("Step 4: Analysing Top 3 stocks...")
    analyses = []
 
    for stock in top3:
        ticker = stock['ticker']
        name = stock['name']
        print(f"  Analysing {ticker} — {name}...")
 
        prompt = """You are a multi-agent investment analysis team analysing a Singapore-listed stock for a retail investor.
 
Stock: {name} ({ticker})
Current Price: SGD {price}
P/E Ratio: {pe}
P/B Ratio: {pb}
Dividend Yield: {div:.1f}%
3-Month Momentum: {mom:.1f}%
Sector: {sector}
Fundamental Score: {score}/100
 
Macro Context: {macro}
 
IMPORTANT: You MUST search for recent news before giving any verdict. This is mandatory.
After you finish searching, you MUST write out the complete final analysis below in your
final message. Do not stop after searching — searching is only step 1, the analysis below
is the actual deliverable and is mandatory.
 
Step 1 — SEARCH FOR NEWS FIRST:
Search "{name} news 2026" and "{ticker} Singapore news"
Look specifically for:
- Government policy changes affecting this company or sector
- Regulatory announcements (Singapore MAS, SGX, foreign governments)
- Management changes or profit warnings
- Geopolitical events affecting the sector (e.g. export controls, tariffs)
- Any news in the last 30 days that could change the investment thesis
 
Step 2 — PRE-BUY NEWS CHECK (MANDATORY SECTION):
⚠️ NEWS RISK ALERT
Rate the news risk: GREEN (no concerning news) / AMBER (monitor) / RED (thesis changed)
List any specific news items found that affect this stock.
If RED: Override the fundamental score — do NOT recommend buying regardless of metrics.
 
Step 3 — FUNDAMENTAL ANALYSIS:
BULL CASE (2-3 points why this stock could go up)
BEAR CASE (2-3 points of risk including any news found above)
SENTIMENT: Current market sentiment on this stock
 
Step 4 — VERDICT:
VERDICT: BUY / HOLD / AVOID with confidence level (High/Medium/Low)
— If news risk is RED, verdict must be AVOID regardless of fundamentals
ENTRY PRICE: Suggested entry price or range
TARGET PRICE: 12-month price target (adjust if news changes thesis)
STOP LOSS: Suggested stop loss level
POSITION SIZE: Suggested % of portfolio (conservative: 1-5%; 0% if RED news risk)
 
Be specific to Singapore market context. Always prioritise recent news over historical metrics.
Remember: Steps 2, 3, and 4 above MUST all appear, fully written out, in your final message.""".format(
            name=name, ticker=ticker,
            price=stock.get('price', 'N/A'),
            pe=stock.get('pe_ratio', 'N/A'),
            pb=stock.get('pb_ratio', 'N/A'),
            div=(stock.get('dividend_yield', 0) or 0) * 100,
            mom=stock.get('momentum_3m', 0),
            sector=stock.get('sector', 'Unknown'),
            score=stock.get('score', 0),
            macro=macro_summary[:200]
        )
 
        text = run_agentic_search(prompt, max_tokens=1800, max_turns=6)
 
        analyses.append({
            'ticker': ticker,
            'name': name,
            'score': stock.get('score', 0),
            'price': stock.get('price', 0),
            'dividend_yield': (stock.get('dividend_yield', 0) or 0) * 100,
            'pe_ratio': stock.get('pe_ratio', 'N/A'),
            'momentum': stock.get('momentum_3m', 0),
            'sector': stock.get('sector', 'Unknown'),
            'analysis': text
        })
 
    print("  Top 3 analysis complete.")
    return analyses
 
 
# ── STEP 5: GENERATE HTML REPORT ─────────────────────────────────────
def generate_html_report(macro_data, micro_text, top30, analyses,
                          sentiment_html="", linkedin_post=""):
    print("Step 5: Generating HTML report...")
 
    verdict = macro_data.get('verdict', {})
    if isinstance(verdict, dict):
        environment = verdict.get('overall', 'NEUTRAL')
        key_risk = verdict.get('key_risk', 'Monitor global rate movements')
        key_opportunity = verdict.get('key_opportunity', 'Selective value in SGX blue chips')
    else:
        environment = 'NEUTRAL'
        key_risk = 'Monitor developments closely'
        key_opportunity = 'Selective opportunities available'
 
    env_colour = {"RISK-ON": "#1a7a45", "RISK-OFF": "#c0392b", "NEUTRAL": "#b7700a"}.get(environment, "#1a5276")
 
    top30_rows = ""
    for i, s in enumerate(top30[:15]):
        # dividend_yield is a clean decimal fraction from sgx_scanner.py
        # (e.g. 0.024 = 2.4%) — single conversion, no guessing.
        div = (s.get('dividend_yield', 0) or 0) * 100
        pe = s.get('pe_ratio', '-')
        pe_str = f"{pe:.1f}" if isinstance(pe, float) else str(pe) if pe else '-'
        mom = s.get('momentum_3m', 0)
        mom_col = "#1a7a45" if mom > 0 else "#c0392b"
        top30_rows += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;color:#1a5276">{i+1}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold">{s['ticker']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{s['name'][:28]}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{s.get('sector','')[:18]}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;text-align:right">{pe_str}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;color:#1a7a45">{div:.1f}%</td>
            <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;color:{mom_col}">{mom:+.1f}%</td>
            <td style="padding:8px;border-bottom:1px solid #eee;text-align:center;background:#e8f5ee;font-weight:bold;color:#1a5276">{s.get('score',0)}</td>
        </tr>"""
 
    analysis_cards = ""
    colours = ["#1a5276", "#0e6655", "#6e2f8a"]
    for i, a in enumerate(analyses):
        col = colours[i % len(colours)]
        analysis_text = a['analysis'].replace('\n', '<br>').replace('**', '')
        analysis_cards += f"""
        <div style="background:white;border-radius:12px;padding:24px;margin-bottom:24px;box-shadow:0 2px 12px rgba(0,0,0,0.08);border-left:5px solid {col}">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px">
                <div>
                    <span style="background:{col};color:white;padding:4px 12px;border-radius:20px;font-size:13px;font-weight:bold">#{i+1} TOP PICK</span>
                    <h3 style="margin:8px 0 4px;color:{col};font-size:20px">{a['ticker']} — {a['name']}</h3>
                    <div style="color:#666;font-size:13px">{a['sector']} &nbsp;·&nbsp; Score: <strong>{a['score']}/100</strong></div>
                </div>
                <div style="text-align:right">
                    <div style="font-size:22px;font-weight:bold;color:{col}">SGD {a['price']:.3f}</div>
                    <div style="color:#1a7a45;font-size:13px">Div: {a['dividend_yield']:.1f}% &nbsp;·&nbsp; P/E: {str(a['pe_ratio'])[:5]}</div>
                    <div style="color:{'#1a7a45' if a['momentum']>0 else '#c0392b'};font-size:13px">3M: {a['momentum']:+.1f}%</div>
                </div>
            </div>
            <div style="background:#f8f9fa;border-radius:8px;padding:16px;font-size:13px;line-height:1.7;color:#333">
                {analysis_text}
            </div>
        </div>"""
 
    global_signals = macro_data.get('global_signals', {})
    if isinstance(global_signals, dict):
        signals_html = "".join([f"<div style='margin-bottom:8px'><span style='color:#1a5276;font-weight:bold'>{k}:</span> {v}</div>" for k, v in global_signals.items()])
    else:
        signals_html = f"<div style='color:#333'>{str(global_signals)[:500]}</div>"
 
    sg_signals = macro_data.get('singapore_signals', {})
    if isinstance(sg_signals, dict):
        sg_html = "".join([f"<div style='margin-bottom:8px'><span style='color:#1a5276;font-weight:bold'>{k}:</span> {v}</div>" for k, v in sg_signals.items()])
    else:
        sg_html = f"<div style='color:#333'>{str(sg_signals)[:300]}</div>"
 
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SGX Weekly Intel — {WEEK_STR}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; background: #f0f2f5; color: #333; }}
  .header {{ background: linear-gradient(135deg, #1a2f4a 0%, #1a5276 100%); color: white; padding: 32px 24px; text-align: center; }}
  .header h1 {{ font-size: 28px; margin-bottom: 8px; }}
  .header p {{ color: #aabbcc; font-size: 14px; }}
  .badge {{ display: inline-block; padding: 4px 16px; border-radius: 20px; font-size: 12px; font-weight: bold; margin: 4px; }}
  .container {{ max-width: 900px; margin: 0 auto; padding: 24px 16px; }}
  .section {{ background: white; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
  .section-title {{ font-size: 16px; font-weight: bold; color: #1a5276; border-bottom: 2px solid #1a5276; padding-bottom: 8px; margin-bottom: 16px; text-transform: uppercase; letter-spacing: 1px; }}
  .signal-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  .signal-box {{ background: #f8f9fa; border-radius: 8px; padding: 16px; }}
  .signal-box h4 {{ color: #1a5276; margin-bottom: 8px; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #1a5276; color: white; padding: 10px 8px; text-align: left; }}
  .footer {{ text-align: center; color: #999; font-size: 12px; padding: 24px; }}
  @media(max-width:600px) {{ .signal-grid {{ grid-template-columns: 1fr; }} table {{ font-size: 11px; }} }}
</style>
</head>
<body>
 
<div class="header">
  <div style="font-size:12px;color:#aabbcc;letter-spacing:2px;margin-bottom:8px">STEVENNGWEALTH.SG · MEMBERS ONLY</div>
  <h1>📊 SGX Weekly Intel Report</h1>
  <p>Week of {WEEK_STR} &nbsp;·&nbsp; Generated {GENERATED}</p>
  <div style="margin-top:12px">
    <span class="badge" style="background:{env_colour};color:white">Market: {environment}</span>
    <span class="badge" style="background:#c9982a;color:white">Top 3 Picks Inside</span>
    <span class="badge" style="background:#2e5a9c;color:white">📰 Sentiment Filter Active</span>
  </div>
</div>
 
<div class="container">
 
  <!-- MACRO SNAPSHOT -->
  <div class="section">
    <div class="section-title">🌍 Macro Snapshot</div>
    <div class="signal-grid">
      <div class="signal-box">
        <h4>Global Signals</h4>
        {signals_html}
      </div>
      <div class="signal-box">
        <h4>Singapore Signals</h4>
        {sg_html}
        <div style="margin-top:12px;padding:10px;background:white;border-radius:6px;border-left:3px solid {env_colour}">
          <div style="font-size:12px;color:#666">Market Environment</div>
          <div style="font-weight:bold;color:{env_colour};font-size:16px">{environment}</div>
        </div>
      </div>
    </div>
    <div style="margin-top:16px;display:grid;grid-template-columns:1fr 1fr;gap:12px">
      <div style="background:#fff8e8;border-radius:8px;padding:12px;border-left:3px solid #c9982a">
        <div style="font-size:11px;color:#888;font-weight:bold">⚠️ KEY RISK THIS WEEK</div>
        <div style="margin-top:4px;font-size:13px">{key_risk}</div>
      </div>
      <div style="background:#e8f5ee;border-radius:8px;padding:12px;border-left:3px solid #1a7a45">
        <div style="font-size:11px;color:#888;font-weight:bold">💡 KEY OPPORTUNITY</div>
        <div style="margin-top:4px;font-size:13px">{key_opportunity}</div>
      </div>
    </div>
  </div>
 
  <!-- MICRO EVENTS -->
  <div class="section">
    <div class="section-title">📰 This Week's Corporate Events</div>
    <div style="background:#f8f9fa;border-radius:8px;padding:16px;font-size:13px;line-height:1.8;color:#333">
      {micro_text.replace(chr(10), '<br>').replace('**','').replace('##','').replace('#','')}
    </div>
  </div>
 
  <!-- SENTIMENT ANALYSIS -->
  {sentiment_html}
 
  <!-- TOP 3 PICKS -->
  <div class="section">
    <div class="section-title">🎯 Top 3 SGX Picks This Week</div>
    <p style="color:#666;font-size:13px;margin-bottom:16px">Multi-agent analysis: fundamental, sentiment, technical, risk · For educational purposes only · Not financial advice</p>
    {analysis_cards}
  </div>
 
  <!-- TOP 30 TABLE -->
  <div class="section">
    <div class="section-title">📋 Top 15 SGX Screener Results</div>
    <p style="color:#666;font-size:12px;margin-bottom:12px">Filtered from {len(top30)}+ candidates · Scored on valuation, dividend, momentum, debt, macro overlay</p>
    <div style="overflow-x:auto">
    <table>
      <thead>
        <tr>
          <th>#</th><th>Ticker</th><th>Name</th><th>Sector</th>
          <th style="text-align:right">P/E</th>
          <th style="text-align:right">Div%</th>
          <th style="text-align:right">3M%</th>
          <th style="text-align:center">Score</th>
        </tr>
      </thead>
      <tbody>{top30_rows}</tbody>
    </table>
    </div>
  </div>
 
  <!-- WEEKLY LINKEDIN POST -->
  <div class="section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
      <div class="section-title" style="margin-bottom:0;border-bottom:none;padding-bottom:0">🔗 This Week's LinkedIn Post</div>
      <button onclick="navigator.clipboard.writeText(document.getElementById('linkedin-post-text').innerText)" style="background:#1a5276;color:white;border:none;padding:8px 16px;border-radius:6px;font-size:13px;cursor:pointer;font-weight:bold">Copy</button>
    </div>
    <div id="linkedin-post-text" style="background:#f8f9fa;border-radius:8px;padding:16px;font-size:14px;line-height:1.7;color:#333;white-space:pre-wrap">{linkedin_post}</div>
  </div>
 
  <!-- DISCLAIMER -->
  <div style="background:#fff8e8;border-radius:8px;padding:16px;font-size:12px;color:#666;line-height:1.6">
    <strong>Disclaimer:</strong> This report is generated by AI for educational and informational purposes only. It does not constitute financial advice. Past performance is not indicative of future results. Always conduct your own research and consult a licensed financial advisor before making investment decisions. Steven Ng is not a licensed financial adviser representative for equities.
  </div>
 
</div>
 
<div class="footer">
  stevenngwealth.sg &nbsp;·&nbsp; Members Area &nbsp;·&nbsp; Generated {GENERATED}<br>
  Powered by Claude AI · SGX Data via Yahoo Finance · Sentiment Layer v2
</div>
 
</body>
</html>"""
 
    print("  HTML report generated.")
    return html
 
 
# ── STEP 6: PUSH TO GIST ─────────────────────────────────────────────
def push_to_gist(html_content):
    print("Step 6: Pushing to Gist...")
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {
        "Authorization": f"token {GIST_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    payload = {
        "description": f"SGX Weekly Intel Report — {WEEK_STR}",
        "files": {
            "sgx_weekly_report.html": {
                "content": html_content
            }
        }
    }
    response = requests.patch(url, headers=headers, json=payload)
    if response.status_code == 200:
        data = response.json()
        raw_url = data['files']['sgx_weekly_report.html']['raw_url']
        print(f"  Pushed to Gist successfully!")
        print(f"  Raw URL: {raw_url}")
        return raw_url
    else:
        print(f"  ERROR pushing to Gist: {response.status_code} — {response.text}")
        return None
 
 
# ── MAIN ─────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"SGX Weekly Intel Report — {WEEK_STR}")
    print("=" * 60)
 
    try:
        # Step 1: Macro
        macro_data = get_macro_analysis()
 
        # Step 2: Micro
        micro_text = get_micro_analysis()
 
        # Step 2b: Weekly LinkedIn post (insurance/wealth angle)
        linkedin_post = get_linkedin_post(macro_data, micro_text)
 
        # Step 3: Scanner
        top30, top3 = run_sgx_scanner(macro_data)
 
        # Step 3b: Sentiment filter
        stocks_to_analyse, sentiment_output = run_sentiment_filter(top3)
 
        # Step 4: TradingAgents analysis (only on sentiment-passed stocks)
        macro_summary = str(macro_data.get('verdict', ''))
        analyses = analyse_top3(stocks_to_analyse, macro_summary)
 
        # Step 5: Generate HTML with sentiment + LinkedIn post sections
        html = generate_html_report(
            macro_data, micro_text, top30, analyses,
            sentiment_html=sentiment_output["sentiment_html"],
            linkedin_post=linkedin_post
        )
 
        # Step 6: Push to Gist
        raw_url = push_to_gist(html)
 
        print("\n" + "=" * 60)
        print("✅ SGX Weekly Intel Report complete!")
        print(f"   Week: {WEEK_STR}")
        print(f"   Sentiment passed: {len(stocks_to_analyse)}/{len(top3)} stocks")
        print(f"   Top picks: {[a['ticker'] for a in analyses]}")
        if raw_url:
            print(f"   Gist URL: {raw_url}")
        print("=" * 60)
 
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
 
if __name__ == "__main__":
    main()
