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

    # Step 1: Let Claude search for live data
    search_prompt = f"""Today is {date_str}. Search for the latest Singapore property market data.

Please search for:
1. "Singapore HDB resale prices {now_sgt.strftime('%B %Y')}"
2. "Singapore private condo prices psf {now_sgt.strftime('%B %Y')}"
3. "Singapore SORA mortgage rates {now_sgt.strftime('%B %Y')}"
4. "Singapore property market news {now_sgt.strftime('%B %Y')}"

After searching, summarize the key findings in plain text — actual numbers, trends, and any notable news."""

    messages = [{"role": "user", "content": search_prompt}]

    # Agentic loop — keep going until no more tool calls
    max_iterations = 8
    iteration = 0
    search_summary = ""

    while iteration < max_iterations:
        iteration += 1
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages
        )

        # Collect text from this response
        for block in response.content:
            if hasattr(block, "text") and block.text:
                search_summary += block.text + "\n"

        # Check stop reason
        if response.stop_reason == "end_turn":
            print(f"  Search complete after {iteration} iteration(s)")
            break
        elif response.stop_reason == "tool_use":
            # Add assistant response to messages
            messages.append({"role": "assistant", "content": response.content})
            # Add tool results
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "Search executed successfully."
                    })
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
        else:
            break

    print(f"  Search summary length: {len(search_summary)} chars")
    if search_summary:
        print(f"  Preview: {search_summary[:150]}")

    # Step 2: Generate structured JSON report from search findings
    json_prompt = f"""Based on this Singapore property market research for {date_str}:

{search_summary if search_summary else "Use your knowledge of Singapore property market as of early 2026."}

Generate a daily property intelligence report. Return ONLY a valid JSON object — absolutely no markdown, no explanation, no text before or after the JSON:

{{
  "date": "{date_str}",
  "time": "{time_str}",
  "must_know": [
    "Most important Singapore property insight today with real data (max 20 words)",
    "Second key market point",
    "Third point relevant to Singapore buyers"
  ],
  "market_pulse": [
    "HDB resale: include actual price figure or trend from research",
    "Private condo: include actual psf or price data",
    "Mortgage rates: include actual SORA or rate figure"
  ],
  "policy_watch": [
    "Most relevant current Singapore property policy",
    "Second policy point — ABSD, TDSR, or cooling measure"
  ],
  "talking_points": [
    "Data-backed talking point for HDB upgrader clients (max 20 words)",
    "Practical fact for first-time buyers (max 20 words)",
    "Market insight for property investors (max 20 words)"
  ],
  "linkedin_post": "Steven Ng voice. Honest, no hype, data-driven. Use 1 real number from research. Target HDB upgraders and first-time buyers. 1 stat + 1 insight + 1 call to action. End: Follow me for daily Singapore property insights. Max 150 words. Line breaks between paragraphs. #SingaporeProperty #HDB #PropertyMarket #RealEstate"
}}

CRITICAL: Output ONLY the JSON object. Start with {{ and end with }}. No other text whatsoever."""

    json_response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": json_prompt}]
    )

    raw = json_response.content[0].text.strip()
    print(f"  Raw JSON response preview: {raw[:100]}")

    # Clean any accidental markdown
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            p = part.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                raw = p
                break

    # Extract JSON object
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
    print("Step 1: Searching for latest Singapore property data...")
    report = generate_report()
    print(f"Step 2: Report generated successfully.")
    print(f"  Must-know: {report['must_know'][0]}")
    print(f"  Market:    {report['market_pulse'][0]}")
    update_gist(report)
    print("Done! Live report published to Members Portal.")
