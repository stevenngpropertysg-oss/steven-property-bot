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
URA_ACCESS_KEY = os.environ["URA_ACCESS_KEY"]

# ─────────────────────────────────────────
# SOURCE 1: URA — Private Condo Transactions
# ─────────────────────────────────────────
def get_ura_data():
    try:
        # Step 1: generate daily token
        token_resp = requests.get(
            "https://www.ura.gov.sg/uraDataService/insertNewToken.action",
            headers={"AccessKey": URA_ACCESS_KEY},
            timeout=15
        )
        token_data = token_resp.json()
        if token_data.get("Status") != "Success":
            return "URA token generation failed."
        token = token_data["Result"]

        # Step 2: get private residential transactions (past quarter)
        txn_resp = requests.get(
            "https://www.ura.gov.sg/uraDataService/invokeUraDS?service=PMI_Resi_Transaction&batch=1",
            headers={"AccessKey": URA_ACCESS_KEY, "Token": token},
            timeout=20
        )
        txn_data = txn_resp.json()
        if txn_data.get("Status") != "Success":
            return "URA transaction data unavailable."

        results = txn_data.get("Result", [])
        if not results:
            return "No URA transaction data available."

        # Summarise recent transactions
        prices = []
        districts = {}
        for project in results[:50]:  # sample first 50 projects
            for txn in project.get("transaction", [])[:3]:
                try:
                    psf = float(txn.get("price", 0)) / float(txn.get("area", 1))
                    prices.append(psf)
                    d = project.get("district", "unknown")
                    districts[d] = districts.get(d, 0) + 1
                except:
                    pass

        if prices:
            avg_psf = sum(prices) / len(prices)
            top_district = max(districts, key=districts.get) if districts else "N/A"
            return (f"Latest URA private condo data: avg ~S${avg_psf:.0f} psf across sampled transactions. "
                    f"Most active district: {top_district}. Based on {len(prices)} recent transactions.")
        return "URA data retrieved but insufficient transaction details."

    except Exception as e:
        return f"URA data fetch error: {str(e)[:80]}"


# ─────────────────────────────────────────
# SOURCE 2: data.gov.sg — HDB Resale Prices
# ─────────────────────────────────────────
def get_hdb_data():
    try:
        # HDB resale price index dataset
        resp = requests.get(
            "https://data.gov.sg/api/action/datastore_search"
            "?resource_id=f1765b54-a209-4718-8d38-a39237f502b3"
            "&limit=5&sort=quarter desc",
            timeout=15
        )
        data = resp.json()
        records = data.get("result", {}).get("records", [])
        if not records:
            return "HDB resale price index data unavailable."

        latest = records[0]
        quarter = latest.get("quarter", "N/A")
        index = latest.get("index", "N/A")

        # Also get recent resale transactions for volume
        txn_resp = requests.get(
            "https://data.gov.sg/api/action/datastore_search"
            "?resource_id=adbbddd3-30e2-445f-a123-29bee150a6fe"
            "&limit=100&sort=month desc",
            timeout=15
        )
        txn_data = txn_resp.json()
        txn_records = txn_data.get("result", {}).get("records", [])

        if txn_records:
            prices = [float(r["resale_price"]) for r in txn_records if r.get("resale_price")]
            avg_price = sum(prices) / len(prices) if prices else 0
            latest_month = txn_records[0].get("month", "N/A")
            return (f"HDB resale: Price index {index} (Q{quarter}). "
                    f"Latest {latest_month} avg resale price ~S${avg_price:,.0f} "
                    f"from {len(prices)} sampled transactions.")

        return f"HDB resale price index: {index} as of {quarter}."

    except Exception as e:
        return f"HDB data fetch error: {str(e)[:80]}"


# ─────────────────────────────────────────
# SOURCE 3: MAS — Interest Rates
# ─────────────────────────────────────────
def get_mas_data():
    try:
        resp = requests.get(
            "https://eservices.mas.gov.sg/api/action/datastore_search"
            "?resource_id=9a0bf149-308c-4bd2-832d-76c8e6cb47ed"
            "&limit=3&sort=end_of_day desc",
            timeout=15
        )
        data = resp.json()
        records = data.get("result", {}).get("records", [])
        if not records:
            return "MAS interest rate data unavailable."

        latest = records[0]
        date = latest.get("end_of_day", "N/A")
        # Look for SORA or relevant mortgage rate fields
        sora = latest.get("comp_sora_1m", latest.get("on_rmb_fx_swap_rate", "N/A"))
        return f"MAS data as of {date}: Compounded SORA 1M ~{sora}%. Mortgage rates remain a key factor for buyer affordability."

    except Exception as e:
        return f"MAS data fetch error: {str(e)[:80]}"


# ─────────────────────────────────────────
# SOURCE 4: SRX RSS — Market Flash News
# ─────────────────────────────────────────
def get_srx_data():
    try:
        import xml.etree.ElementTree as ET
        resp = requests.get(
            "https://www.srx.com.sg/rss/news",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        root = ET.fromstring(resp.content)
        items = root.findall(".//item")
        headlines = []
        for item in items[:5]:
            title = item.findtext("title", "").strip()
            if title and "property" in title.lower() or "hdb" in title.lower() or "condo" in title.lower() or "resale" in title.lower() or "price" in title.lower():
                headlines.append(title)
        if headlines:
            return "SRX latest headlines: " + " | ".join(headlines[:3])
        # fallback: return any top 3 headlines
        all_titles = [item.findtext("title", "").strip() for item in items[:3]]
        return "SRX news: " + " | ".join(t for t in all_titles if t)
    except Exception as e:
        return f"SRX feed unavailable: {str(e)[:80]}"


# ─────────────────────────────────────────
# GENERATE REPORT VIA CLAUDE
# ─────────────────────────────────────────
def generate_report(ura, hdb, mas, srx):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    PROMPT = f"""You are generating a daily Singapore property intelligence report for {date_str}.

Below is REAL LIVE DATA fetched this morning from official Singapore sources.
Use these numbers and facts directly in your report — do not invent figures.

=== LIVE DATA ===
[URA Private Condo]: {ura}
[HDB Resale]: {hdb}
[MAS Interest Rates]: {mas}
[SRX Market News]: {srx}
=================

Return ONLY a valid JSON object with EXACTLY this structure — no markdown, no explanation, no extra text:

{{
  "date": "{date_str}",
  "time": "{time_str}",
  "must_know": [
    "Most important insight from today's live data (1 sentence, max 20 words)",
    "Second key point grounded in the data above",
    "Third key point relevant to Singapore buyers today"
  ],
  "market_pulse": [
    "HDB resale: use the actual figures from HDB live data above",
    "Private condo: use the actual URA figures above",
    "Rental/rates: use MAS rate data and any rental insight"
  ],
  "policy_watch": [
    "Most relevant current policy point for Singapore property buyers",
    "Second policy reminder (cooling measures, ABSD, TDSR etc)"
  ],
  "talking_points": [
    "Fact for HDB upgrader clients — use real data from above",
    "Fact for first-time buyers — practical and grounded",
    "Fact for property investors — yield or price trend insight"
  ],
  "linkedin_post": "Ready-to-post LinkedIn caption in Steven Ng voice. Data-driven, honest, no hype. Use at least 1 real number from today's data. Target HDB upgraders and first-time buyers in Singapore. Include 1 key stat, 1 insight, 1 soft call to action. End with: Follow me for daily Singapore property insights. Max 150 words. Use line breaks. #SingaporeProperty #HDB #PropertyMarket #RealEstate"
}}

Rules:
- Ground every bullet in the LIVE DATA above — no invented figures
- Return ONLY the JSON. No markdown fences. No preamble.
- Each bullet under 25 words. Double quotes only. Valid JSON."""

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


# ─────────────────────────────────────────
# UPDATE GIST
# ─────────────────────────────────────────
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


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
if __name__ == "__main__":
    print(f"Generating live property intel for {date_str}...")

    print("Fetching URA private condo data...")
    ura = get_ura_data()
    print(f"  URA: {ura[:80]}...")

    print("Fetching HDB resale data...")
    hdb = get_hdb_data()
    print(f"  HDB: {hdb[:80]}...")

    print("Fetching MAS interest rate data...")
    mas = get_mas_data()
    print(f"  MAS: {mas[:80]}...")

    print("Fetching SRX market news...")
    srx = get_srx_data()
    print(f"  SRX: {srx[:80]}...")

    print("Generating report with Claude...")
    report = generate_report(ura, hdb, mas, srx)
    print(f"  Must-know[0]: {report['must_know'][0]}")

    update_gist(report)
    print("Done! Live report published to Members Portal.")
