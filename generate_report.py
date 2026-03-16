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

def generate_report():
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""Today is {date_str}. You are generating a daily Singapore property intelligence briefing for stevenngproperty.sg members.

Use your web_search tool to find the LATEST information on these topics:
1. Search "Singapore HDB resale prices {now_sgt.strftime('%B %Y')}" - find latest median prices and transaction volumes
2. Search "Singapore private condo prices {now_sgt.strftime('%B %Y')}" - find latest URA or SRX data
3. Search "Singapore mortgage rates SORA {now_sgt.strftime('%B %Y')}" - find current mortgage rates
4. Search "Singapore property news {now_sgt.strftime('%B %Y')}" - find any notable market news or policy updates

After searching, return ONLY a valid JSON object with this EXACT structure — no markdown, no explanation:

{{
  "date": "{date_str}",
  "time": "{time_str}",
  "must_know": [
    "Most important Singapore property insight today — use real data found (max 20 words)",
    "Second key market point grounded in search results",
    "Third point relevant to Singapore buyers or investors"
  ],
  "market_pulse": [
    "HDB resale: include actual median price or index figure found",
    "Private condo: include actual psf or price data found",
    "Mortgage/rates: include actual SORA or bank rate found"
  ],
  "policy_watch": [
    "Most relevant current Singapore property policy point",
    "Second policy reminder — ABSD rates, TDSR, cooling measures"
  ],
  "talking_points": [
    "Data-backed talking point for HDB upgrader clients",
    "Practical fact for first-time buyers",
    "Market insight for property investors"
  ],
  "linkedin_post": "Ready-to-post LinkedIn caption in Steven Ng's voice. Honest, data-driven, no hype. Include at least 1 real number from today's search. Target HDB upgraders and first-time buyers in Singapore. Structure: 1 key stat + 1 insight + 1 call to action. End with: Follow me for daily Singapore property insights. Max 150 words. Use line breaks for readability. #SingaporeProperty #HDB #PropertyMarket #RealEstate"
}}

Rules:
- Use REAL data from your web searches — do not fabricate numbers
- If a search returns no current data, use the most recent reliable figure you found and note the period
- Return ONLY the JSON object. No markdown fences. No preamble. No explanation.
- Each bullet point max 25 words. Double quotes only. Valid JSON."""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}]
    )

    # Extract the final text response (after tool use)
    raw = ""
    for block in message.content:
        if block.type == "text":
            raw = block.text.strip()

    # Handle any accidental markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    # Find JSON object in response
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        raw = raw[start:end]

    return json.loads(raw)


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
    resp = requests.patch(
        f"https://api.github.com/gists/{GIST_ID}",
        headers=headers,
        json=payload
    )
    resp.raise_for_status()
    print(f"Gist updated for {date_str}")


if __name__ == "__main__":
    print(f"Generating live property intel for {date_str}...")
    print("Searching for latest Singapore property data...")
    report = generate_report()
    print(f"Report generated.")
    print(f"  Must-know: {report['must_know'][0]}")
    print(f"  Market: {report['market_pulse'][0]}")
    update_gist(report)
    print("Done! Live report published to Members Portal.")
