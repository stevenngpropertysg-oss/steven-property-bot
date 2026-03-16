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
URA_ACCESS_KEY = os.environ["URA_ACCESS_KEY"]

HEADERS = {"User-Agent": "StevenNgProperty/1.0 stevenngproperty.sg@gmail.com"}

# ─────────────────────────────────────────
# SOURCE 1: URA — Private Condo Transactions
# ─────────────────────────────────────────
def get_ura_data():
    try:
        # Step 1: generate daily token
        r = requests.get(
            "https://www.ura.gov.sg/uraDataService/insertNewToken.action",
            headers={**HEADERS, "AccessKey": URA_ACCESS_KEY},
            timeout=20
        )
        r.raise_for_status()
        token_data = r.json()
        if token_data.get("Status") != "Success":
            return f"URA token failed: {token_data.get('Message','unknown')}"
        token = token_data["Result"]

        # Step 2: get private residential transactions batch 1
        r2 = requests.get(
            "https://www.ura.gov.sg/uraDataService/invokeUraDS?service=PMI_Resi_Transaction&batch=1",
            headers={**HEADERS, "AccessKey": URA_ACCESS_KEY, "Token": token},
            timeout=25
        )
        r2.raise_for_status()
        data = r2.json()
        if data.get("Status") != "Success":
            return f"URA transaction failed: {data.get('Message','unknown')}"

        results = data.get("Result", [])
        if not results:
            return "URA: No transaction records returned."

        prices_psf = []
        area_types = {}
        for proj in results[:80]:
            ptype = proj.get("marketSegment", "unknown")
            for txn in proj.get("transaction", [])[:5]:
                try:
                    price = float(str(txn.get("price","0")).replace(",",""))
                    area = float(str(txn.get("area","1")).replace(",",""))
                    if price > 0 and area > 0:
                        prices_psf.append(price / area)
                        area_types[ptype] = area_types.get(ptype, 0) + 1
                except:
                    pass

        if prices_psf:
            avg = sum(prices_psf) / len(prices_psf)
            top = max(area_types, key=area_types.get) if area_types else "N/A"
            return (f"URA private condo: avg S${avg:.0f} psf across {len(prices_psf)} "
                    f"recent transactions. Most active segment: {top}.")
        return "URA data retrieved but no valid price records found."

    except Exception as e:
        return f"URA error: {str(e)[:100]}"


# ─────────────────────────────────────────
# SOURCE 2: data.gov.sg — HDB Resale Prices
# ─────────────────────────────────────────
def get_hdb_data():
    try:
        # Updated resource ID for Jan 2017 onwards
        url = ("https://data.gov.sg/api/action/datastore_search"
               "?resource_id=d_8b84c4ee58e3cfc0ece0d773c8ca6abc"
               "&limit=200&sort=month%20desc")
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        records = data.get("result", {}).get("records", [])

        if not records:
            return "HDB resale: No records returned from data.gov.sg."

        # Get latest month's data
        latest_month = records[0].get("month", "N/A")
        month_records = [rec for rec in records if rec.get("month") == latest_month]

        prices = []
        towns = {}
        flat_types = {}
        for rec in month_records:
            try:
                p = float(str(rec.get("resale_price", "0")).replace(",", ""))
                if p > 0:
                    prices.append(p)
                    t = rec.get("town", "unknown")
                    towns[t] = towns.get(t, 0) + 1
                    ft = rec.get("flat_type", "unknown")
                    flat_types[ft] = flat_types.get(ft, 0) + 1
            except:
                pass

        if prices:
            avg = sum(prices) / len(prices)
            median = sorted(prices)[len(prices)//2]
            top_town = max(towns, key=towns.get) if towns else "N/A"
            top_flat = max(flat_types, key=flat_types.get) if flat_types else "N/A"
            return (f"HDB resale ({latest_month}): {len(prices)} transactions. "
                    f"Avg S${avg:,.0f}, median S${median:,.0f}. "
                    f"Most active: {top_town}, {top_flat}.")
        return f"HDB resale data for {latest_month} has no valid price records."

    except Exception as e:
        return f"HDB error: {str(e)[:100]}"


# ─────────────────────────────────────────
# SOURCE 3: MAS — Interest Rates (SORA)
# ─────────────────────────────────────────
def get_mas_data():
    try:
        # MAS domestic interest rates
        url = ("https://eservices.mas.gov.sg/api/action/datastore_search"
               "?resource_id=9a0bf149-308c-4bd2-832d-76c8e6cb47ed"
               "&limit=5&sort=end_of_day%20desc")
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        records = data.get("result", {}).get("records", [])

        if not records:
            # fallback: try prime lending rate dataset
            url2 = ("https://eservices.mas.gov.sg/api/action/datastore_search"
                    "?resource_id=5f2b18a8-0883-4769-a635-879c63d3caac"
                    "&limit=3&sort=end_of_month%20desc")
            r2 = requests.get(url2, headers=HEADERS, timeout=20)
            records2 = r2.json().get("result", {}).get("records", [])
            if records2:
                latest = records2[0]
                date = latest.get("end_of_month", "N/A")
                prime = latest.get("prime_lending_rate", "N/A")
                return f"MAS prime lending rate: {prime}% as of {date}. Key benchmark for mortgage pricing."
            return "MAS interest rate data temporarily unavailable."

        latest = records[0]
        date = latest.get("end_of_day", "N/A")
        # Try various SORA fields
        sora_1m = latest.get("comp_sora_1m", None)
        sora_3m = latest.get("comp_sora_3m", None)
        overnight = latest.get("sora", None)

        parts = []
        if overnight: parts.append(f"SORA overnight {overnight}%")
        if sora_1m: parts.append(f"1M compounded {sora_1m}%")
        if sora_3m: parts.append(f"3M compounded {sora_3m}%")

        if parts:
            return f"MAS rates as of {date}: {', '.join(parts)}. Floating rate mortgages pegged to SORA."
        return f"MAS data retrieved for {date} but specific rate fields not found."

    except Exception as e:
        return f"MAS error: {str(e)[:100]}"


# ─────────────────────────────────────────
# SOURCE 4: SRX — Market News RSS
# ─────────────────────────────────────────
def get_srx_data():
    try:
        import xml.etree.ElementTree as ET
        urls_to_try = [
            "https://www.srx.com.sg/rss/news",
            "https://www.srx.com.sg/rss/flash",
        ]
        for url in urls_to_try:
            try:
                r = requests.get(url, headers=HEADERS, timeout=15)
                if r.status_code == 200:
                    root = ET.fromstring(r.content)
                    items = root.findall(".//item")
                    headlines = []
                    for item in items[:8]:
                        title = item.findtext("title", "").strip()
                        if title:
                            headlines.append(title)
                    if headlines:
                        return "SRX latest: " + " | ".join(headlines[:4])
            except:
                continue

        # Fallback: EdgeProp RSS
        r3 = requests.get("https://www.edgeprop.sg/rss.xml", headers=HEADERS, timeout=15)
        if r3.status_code == 200:
            root = ET.fromstring(r3.content)
            items = root.findall(".//item")
            headlines = [item.findtext("title","").strip() for item in items[:4] if item.findtext("title","")]
            if headlines:
                return "EdgeProp latest: " + " | ".join(headlines[:3])

        return "Property news feeds temporarily unavailable; market conditions remain stable."

    except Exception as e:
        return f"News feed error: {str(e)[:100]}"


# ─────────────────────────────────────────
# GENERATE REPORT VIA CLAUDE
# ─────────────────────────────────────────
def generate_report(ura, hdb, mas, srx):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""You are generating a daily Singapore property intelligence report for {date_str}.

REAL LIVE DATA fetched this morning from official Singapore sources:

[URA Private Condo Data]: {ura}
[HDB Resale Data]: {hdb}
[MAS Interest Rates]: {mas}
[Property News]: {srx}

Use the actual numbers and facts above directly. Do not invent figures.
If a data source shows an error, skip that source and use the others.

Return ONLY a valid JSON object — no markdown, no explanation, no extra text:

{{
  "date": "{date_str}",
  "time": "{time_str}",
  "must_know": [
    "Most important insight from today's live data (1 sentence, max 20 words)",
    "Second key point grounded in the data",
    "Third key point relevant to Singapore buyers"
  ],
  "market_pulse": [
    "HDB resale: use actual figures from HDB data above",
    "Private condo: use actual URA figures above",
    "Interest rates: use MAS rate data above"
  ],
  "policy_watch": [
    "Most relevant current policy for Singapore property buyers",
    "Second policy reminder (ABSD, TDSR, cooling measures)"
  ],
  "talking_points": [
    "Data-backed fact for HDB upgrader clients",
    "Practical fact for first-time buyers",
    "Yield or price insight for investors"
  ],
  "linkedin_post": "Ready-to-post LinkedIn caption in Steven Ng voice. Honest, no hype, numbers-first. Use at least 1 real number from today's data. Target HDB upgraders and first-time buyers. 1 stat + 1 insight + 1 call to action. End: Follow me for daily Singapore property insights. Max 150 words. Line breaks. #SingaporeProperty #HDB #PropertyMarket #RealEstate"
}}

Rules: ONLY JSON. No markdown. No preamble. Each bullet max 25 words. Valid JSON."""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
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
    print(f"✅ Gist updated for {date_str}")


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
if __name__ == "__main__":
    print(f"Fetching live Singapore property data for {date_str}...")

    print("1/4 URA private condo...")
    ura = get_ura_data()
    print(f"    {ura[:100]}")

    print("2/4 HDB resale...")
    hdb = get_hdb_data()
    print(f"    {hdb[:100]}")

    print("3/4 MAS interest rates...")
    mas = get_mas_data()
    print(f"    {mas[:100]}")

    print("4/4 Property news...")
    srx = get_srx_data()
    print(f"    {srx[:100]}")

    print("Generating report with Claude...")
    report = generate_report(ura, hdb, mas, srx)
    print(f"    Must-know: {report['must_know'][0]}")

    update_gist(report)
    print("Done! Live report published to Members Portal. 🏠")
