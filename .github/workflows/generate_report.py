import anthropic
import requests
import json
import os
from datetime import datetime, timezone, timedelta

# SGT = UTC+8
SGT = timezone(timedelta(hours=8))
now_sgt = datetime.now(SGT)
date_str = now_sgt.strftime("%A, %d %b %Y")
time_str = "8:00 AM SGT"

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GIST_TOKEN = os.environ["GIST_TOKEN"]
GIST_ID = os.environ["GIST_ID"]

PROMPT = f"""You are generating a daily Singapore property intelligence report for {date_str}.

Search your knowledge and reason carefully about current Singapore property market conditions.

Return ONLY a valid JSON object with EXACTLY this structure — no markdown, no explanation, no extra text:

{{
  "date": "{date_str}",
  "time": "{time_str}",
  "must_know": [
    "One urgent or notable Singapore property news point today (1 sentence, max 20 words)",
    "Second must-know point",
    "Third must-know point"
  ],
  "market_pulse": [
    "HDB resale: brief trend update with any recent data point",
    "Private condo: brief trend update",
    "Rental market: brief update"
  ],
  "policy_watch": [
    "Most relevant current government/MAS/CEA/HDB policy point",
    "Second policy point or cooling measure reminder"
  ],
  "talking_points": [
    "Fact useful for HDB upgrader clients today (max 20 words)",
    "Fact useful for first-time buyers (max 20 words)",
    "Fact useful for property investors (max 20 words)"
  ],
  "linkedin_post": "Write a ready-to-post LinkedIn caption in Steven Ng's voice.\\n\\nSteven is a data-driven Singapore property advisor with 23 years of engineering background.\\nTone: honest, no hype, numbers-first, approachable.\\nTarget: HDB upgraders and first-time buyers in Singapore.\\nInclude: 1 key market stat, 1 practical insight, 1 soft call to action.\\nEnd with: Follow me for daily Singapore property insights.\\nMax 150 words. Use line breaks for readability.\\nHashtags: #SingaporeProperty #HDB #PropertyMarket #RealEstate"
}}

Rules:
- Return ONLY the JSON. No markdown fences. No preamble.
- Each bullet point must be under 25 words.
- No HTML or special characters inside strings.
- Use double quotes only. Valid JSON required."""

def generate_report():
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": PROMPT}]
    )
    
    raw = message.content[0].text.strip()
    # Strip any accidental markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    
    data = json.loads(raw)
    return data

def update_gist(data):
    headers = {
        "Authorization": f"token {GIST_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    payload = {
        "description": f"Steven Ng Property Daily Intel — {date_str}",
        "files": {
            "property_intel.json": {
                "content": json.dumps(data, ensure_ascii=False, indent=2)
            }
        }
    }
    url = f"https://api.github.com/gists/{GIST_ID}"
    resp = requests.patch(url, headers=headers, json=payload)
    resp.raise_for_status()
    print(f"✅ Gist updated successfully for {date_str}")

if __name__ == "__main__":
    print(f"Generating report for {date_str}...")
    report = generate_report()
    print("Report generated. Sample:")
    print(f"  Must-know[0]: {report['must_know'][0]}")
    update_gist(report)
    print("Done! Members portal will show today's report.")
