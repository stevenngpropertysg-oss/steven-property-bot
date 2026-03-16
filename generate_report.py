import anthropic
import requests
import json
import os
from datetime import datetime, timezone, timedelta

SGT = timezone(timedelta(hours=8))
now_sgt = datetime.now(SGT)
date_str = now_sgt.strftime("%A, %d %b %Y")
time_str = "8:00 AM SGT"

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GIST_TOKEN = os.environ["GIST_TOKEN"]
GIST_ID = os.environ["GIST_ID"]

PROMPT = f"""You are generating a daily Singapore property intelligence report for {date_str}.

Return ONLY a valid JSON object with EXACTLY this structure, no markdown, no explanation:

{{
  "date": "{date_str}",
  "time": "{time_str}",
  "must_know": ["Point 1 (max 20 words)", "Point 2", "Point 3"],
  "market_pulse": ["HDB resale: update", "Private condo: update", "Rental market: update"],
  "policy_watch": ["Policy point 1", "Policy point 2"],
  "talking_points": ["For HDB upgraders", "For first-time buyers", "For investors"],
  "linkedin_post": "Ready-to-post caption in Steven Ng voice. Data-driven, honest, no hype. Target HDB upgraders and first-time buyers. 1 stat, 1 insight, 1 call to action. End with: Follow me for daily Singapore property insights. Max 150 words. #SingaporeProperty #HDB #PropertyMarket #RealEstate"
}}

Rules: Return ONLY the JSON. No markdown fences. No preamble. Double quotes only. Valid JSON."""

def generate_report():
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": PROMPT}]
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

def update_gist(data):
    headers = {"Authorization": f"token {GIST_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    payload = {"description": f"Steven Ng Property Daily Intel — {date_str}", "files": {"property_intel.json": {"content": json.dumps(data, ensure_ascii=False, indent=2)}}}
    resp = requests.patch(f"https://api.github.com/gists/{GIST_ID}", headers=headers, json=payload)
    resp.raise_for_status()
    print(f"Gist updated for {date_str}")

if __name__ == "__main__":
    print(f"Generating report for {date_str}...")
    report = generate_report()
    print(f"  Must-know[0]: {report['must_know'][0]}")
    update_gist(report)
    print("Done!")
