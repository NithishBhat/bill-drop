"""Backend for Bill Drop, the Massachusetts electricity bill coach.

Contract with the frontend (don't rename keys without telling your teammate):
    read_bill(image_bytes) -> bill dict (see BILL_SCHEMA)
    find_savings(bill)     -> {"yearly_cost_now", "total_yearly_savings", "recommendations": [...]}
    ask(question, bill, savings) -> {"answer": str, "sources": [str]}
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import boto3

REGION = "us-east-1"
# Override with BILL_MODEL_ID=us.amazon.nova-pro-v1:0 if Claude access isn't approved yet.
MODEL_ID = os.environ.get("BILL_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
# Set by Terraform on the server (`terraform output knowledge_base_id` for local runs).
KNOWLEDGE_BASE_ID = os.environ.get("KNOWLEDGE_BASE_ID", "")

_bedrock = boto3.client("bedrock-runtime", region_name=REGION)
_kb = boto3.client("bedrock-agent-runtime", region_name=REGION)

# The exact keys read_bill returns. Claude is forced to fill this via a tool call,
# so the output is always valid JSON in this shape.
BILL_SCHEMA = {
    "type": "object",
    "properties": {
        "utility": {"type": ["string", "null"], "description": "Utility company, e.g. Eversource, National Grid"},
        "account_number": {"type": ["string", "null"]},
        "billing_start": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "billing_end": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "kwh_used": {"type": ["number", "null"], "description": "Electricity used this billing period, in kWh"},
        "total_cost": {"type": ["number", "null"], "description": "Total amount due for this period's electricity charges, in dollars"},
        "supply_rate_per_kwh": {"type": ["number", "null"], "description": "Supply/generation price in $/kWh, e.g. 0.17323 (NOT cents)"},
        "supplier": {"type": ["string", "null"], "description": "Who supplies the electricity, e.g. 'Basic Service', 'Boston Community Choice Electricity'"},
        # Enrollment-form fields (used by assisted_fill.py for the BCCE sign-up form).
        "name_on_account": {"type": ["string", "null"], "description": "Customer name as printed on the bill"},
        "service_street": {"type": ["string", "null"], "description": "Service address street line, e.g. '110 Example St Apt 2'"},
        "service_city": {"type": ["string", "null"]},
        "service_zip": {"type": ["string", "null"], "description": "5-digit ZIP"},
        "service_reference_number": {"type": ["string", "null"], "description": "Eversource 'Service Reference' number (8 digits, page 2 under the meter number)"},
        "meter_number": {"type": ["string", "null"]},
        "customer_name_key": {"type": ["string", "null"], "description": "Eversource customer name key: 4 capital letters, often unlabeled, top left of page 2 below the account number"},
        "monthly_kwh_history": {
            "type": "array",
            "description": "Usage history chart on the bill, oldest first. Empty list if the bill has none.",
            "items": {
                "type": "object",
                "properties": {
                    "month": {"type": "string", "description": "YYYY-MM"},
                    "kwh": {"type": "number"},
                },
                "required": ["month", "kwh"],
            },
        },
    },
    "required": ["utility", "account_number", "billing_start", "billing_end", "kwh_used",
                 "total_cost", "supply_rate_per_kwh", "supplier", "name_on_account", "service_street",
                 "service_city", "service_zip", "service_reference_number", "meter_number",
                 "customer_name_key", "monthly_kwh_history"],
}

READ_PROMPT = """This is a Massachusetts residential electricity bill. Extract the fields with the save_bill tool.
- Copy numbers exactly as printed. Use null for anything not on the bill; never guess.
- supply_rate_per_kwh is the Supply/Generation charge per kWh in dollars (convert cents to dollars).
- If there's a usage history bar chart, read each month's kWh as best you can.
- Copy account, service reference and meter numbers exactly, including leading zeros (as strings)."""

# What the model fills in for find_savings. supply_switch ideas give a new rate and
# Python does the math; other ideas give the model's estimate from the KB docs.
SAVINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["supply_switch", "rebate"]},
                    "title": {"type": "string", "description": "Short action, e.g. 'Switch to BCCE Basic'"},
                    "why": {"type": "string", "description": "1-2 sentences tied to THIS bill's numbers, plus any eligibility condition"},
                    "new_supply_rate_per_kwh": {"type": ["number", "null"], "description": "supply_switch only: the new plan's $/kWh from the documents"},
                    "estimated_yearly_savings": {"type": ["number", "null"], "description": "rebate only: rough $/year estimate, null if the documents give no basis"},
                    "link": {"type": ["string", "null"], "description": "URL from the documents, or null"},
                },
                "required": ["kind", "title", "why", "new_supply_rate_per_kwh", "estimated_yearly_savings", "link"],
            },
        },
    },
    "required": ["recommendations"],
}

SAVINGS_PROMPT = """You are an energy bill coach for Massachusetts households.
Using ONLY the reference documents below, recommend 2-4 ways this household can save money. Use the save_recommendations tool.
- supply_switch: a cheaper supply plan available to this utility's customers. Give its exact $/kWh rate from the documents; only suggest it if cheaper than the current supply rate. Mention eligibility limits (e.g. BCCE is only for Eversource customers in the City of Boston).
  For competitive supplier offers (Energy Switch MA), also mention cancellation fees, automatic renewal and new-customer-only terms.
- rebate: Mass Save offers that fit this household's usage pattern (e.g. high winter usage -> heat pumps / insulation).
- Never invent rates, rebate amounts or links that aren't in the documents.
- In "why", do NOT state yearly dollar savings; the app calculates and shows those itself.

Bill facts:
{facts}

Reference documents:
{docs}"""


def _ask_with_tool(content, tool_name, description, schema, max_tokens=2000):
    """Call the model, force it to answer through one tool, and return the tool's input."""
    resp = _bedrock.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": content}],
        toolConfig={
            "tools": [{"toolSpec": {"name": tool_name, "description": description,
                                    "inputSchema": {"json": schema}}}],
            "toolChoice": {"tool": {"name": tool_name}},
        },
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
    )
    usage = resp["usage"]
    print(f"[{tool_name}] {usage['inputTokens']} in / {usage['outputTokens']} out tokens", file=sys.stderr)
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block:
            return block["toolUse"]["input"]
    raise RuntimeError(f"Model didn't call {tool_name}.")


def _image_format(data):
    """Detect the upload type from its first bytes."""
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise ValueError("Unsupported file type. Upload a PNG, JPG, WEBP or PDF bill.")


def read_bill(image_bytes):
    """Send a bill image/PDF to the model on Bedrock and return the bill dict."""
    fmt = _image_format(image_bytes)
    if fmt == "pdf":
        upload = {"document": {"format": "pdf", "name": "bill", "source": {"bytes": image_bytes}}}
    else:
        upload = {"image": {"format": fmt, "source": {"bytes": image_bytes}}}

    bill = _ask_with_tool([upload, {"text": READ_PROMPT}], "save_bill",
                          "Save the fields extracted from the electricity bill.", BILL_SCHEMA)

    # Make sure every contract key exists even if the model left one out.
    for key in BILL_SCHEMA["required"]:
        bill.setdefault(key, [] if key == "monthly_kwh_history" else None)
    return bill


def _yearly_kwh(bill):
    """Last 12 months from the history chart if present, else this month x 12."""
    history = [m["kwh"] for m in bill["monthly_kwh_history"]][-12:]
    if len(history) == 12:
        return sum(history)
    return (bill["kwh_used"] or 0) * 12


def _winter_vs_summer(bill):
    """Ratio of average Dec-Feb usage to average Jun-Aug usage, or None if unknown."""
    winter, summer = [], []
    for m in bill["monthly_kwh_history"]:
        month = int(m["month"][5:7])
        if month in (12, 1, 2):
            winter.append(m["kwh"])
        elif month in (6, 7, 8):
            summer.append(m["kwh"])
    if not winter or not summer or sum(summer) == 0:
        return None
    return (sum(winter) / len(winter)) / (sum(summer) / len(summer))


def _search_kb(query, n=4):
    """Return [(chunk_text, source_file_name), ...] from the Knowledge Base."""
    if not KNOWLEDGE_BASE_ID:
        raise RuntimeError("KNOWLEDGE_BASE_ID is not set. Run `terraform output knowledge_base_id` in infra/.")
    resp = _kb.retrieve(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": n}},
    )
    return [(r["content"]["text"], r["location"]["s3Location"]["uri"].rsplit("/", 1)[-1])
            for r in resp["retrievalResults"]]


def find_savings(bill):
    """Look up cheaper plans and Mass Save offers in the Knowledge Base and price them for this bill."""
    yearly_kwh = _yearly_kwh(bill)
    kwh, cost = bill["kwh_used"], bill["total_cost"]
    yearly_cost_now = round(cost / kwh * yearly_kwh, 2) if kwh and cost else None
    ratio = _winter_vs_summer(bill)

    utility = bill["utility"] or "Massachusetts utility"
    queries = [
        f"cheaper electricity supply plan rates for {utility} residential customers",
        f"{utility} Basic Service residential supply rate",
        f"Energy Switch MA competitive supplier offers for {utility} residential customers",
        "Mass Save rebates heat pumps insulation for high winter electricity usage"
        if ratio and ratio > 1.3 else "Mass Save rebates smart thermostat heat pump water heater",
    ]
    # Run the KB searches at the same time instead of one after another.
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        results = list(pool.map(_search_kb, queries))
    docs = []
    for hits in results:
        for chunk, _ in hits:
            if chunk not in docs:
                docs.append(chunk)

    facts = {
        "utility": bill["utility"],
        "supplier": bill["supplier"],
        "current_supply_rate_per_kwh": bill["supply_rate_per_kwh"],
        "kwh_this_month": kwh,
        "estimated_yearly_kwh": yearly_kwh,
        "estimated_yearly_cost": yearly_cost_now,
        "winter_to_summer_usage_ratio": round(ratio, 2) if ratio else None,
    }
    prompt = SAVINGS_PROMPT.format(facts=json.dumps(facts, indent=2),
                                   docs="\n\n---\n\n".join(docs))
    ideas = _ask_with_tool([{"text": prompt}], "save_recommendations",
                           "Save the savings recommendations for this household.", SAVINGS_SCHEMA)

    recommendations = []
    best_switch = None  # you can only be on one supply plan, so keep just the cheapest
    current_rate = bill["supply_rate_per_kwh"]
    for idea in ideas["recommendations"]:
        link = idea.get("link")
        if link and not link.startswith("http"):
            link = "https://" + link
        new_rate = None
        if idea["kind"] == "supply_switch":
            new_rate = idea.get("new_supply_rate_per_kwh")
            if not (new_rate and current_rate and new_rate < current_rate):
                continue  # can't verify it's cheaper, so don't show it
            savings = (current_rate - new_rate) * yearly_kwh
        else:
            # The model's estimate; never show a negative "saving".
            savings = max(idea.get("estimated_yearly_savings") or 0, 0)
        rec = {
            "title": idea["title"],
            "why": idea["why"],
            "yearly_savings": round(savings, 2),
            "link": link,
            # Supply switches only; assisted_fill.py uses it to pick the BCCE plan.
            "new_supply_rate_per_kwh": new_rate,
        }
        if idea["kind"] == "supply_switch":
            if best_switch is None or rec["yearly_savings"] > best_switch["yearly_savings"]:
                best_switch = rec
        else:
            recommendations.append(rec)
    if best_switch:
        recommendations.append(best_switch)
    recommendations.sort(key=lambda r: r["yearly_savings"], reverse=True)

    return {
        "yearly_cost_now": yearly_cost_now,
        "total_yearly_savings": round(sum(r["yearly_savings"] for r in recommendations), 2),
        "recommendations": recommendations,
    }


SOURCE_NAMES = {
    "ma-electric-supply-rates-2026.md": "MA electricity supply rates (2026)",
    "ma-supply-rates-eversource-2026.md": "Eversource Basic Service rates (2026)",
    "ma-supply-rates-nationalgrid-2026.md": "National Grid Basic Service rates (2026)",
    "ma-supply-rates-bcce-2026.md": "City of Boston: BCCE rates (2026)",
    "ma-supply-switch-savings.md": "How supply-switch savings are estimated",
    "masssave-rebates-2026.md": "Mass Save rebates (2026)",
    "eversource-ma-rates-summary.txt": "Eversource MA rate summary",
    "nationalgrid-ma-basic-service-aug2026.txt": "National Grid Basic Service rates",
    "boston-bcce.txt": "City of Boston: BCCE program page",
    "boston-bcce-march-2026-letter.txt": "City of Boston: BCCE notice (Mar 2026)",
}
# Energy Switch MA topic files (scripts/fetch_energyswitch.py) all cite the same source.
SOURCE_NAMES.update({f"energyswitchma-02130-{t}.md": "Energy Switch MA (MA DPU): supplier offers"
                     for t in ("lowest-rates", "no-cancellation-fee", "100-percent-renewable", "all-offers")})

ASK_PROMPT = """You are Bill Drop, a friendly Massachusetts energy bill coach. Answer the question in 2-5 short sentences
using ONLY the reference documents below (and the user's bill facts, if given). If the documents don't
cover it, say so and suggest calling Mass Save at 866-527-7283. Never invent rates or rebate amounts.
If the bill facts include the app's savings figures, quote those exactly instead of doing your own math.
If the question sets a limit (e.g. "below 16 cents", "no cancellation fee"), check every item against it and
list only the items that meet it.

{bill}Reference documents:
{docs}

Question: {question}"""


def ask(question, bill=None, savings=None):
    """Answer a free-form question from the Knowledge Base, optionally using the user's bill
    and the find_savings result (so the chat quotes the same numbers the page shows)."""
    # 8 chunks so list-style questions ("which suppliers...") see the whole offer summary.
    hits = _search_kb(question, n=8)
    docs = "\n\n---\n\n".join(text for text, _ in hits)
    bill_facts = ""
    if bill:
        keys = ("utility", "supplier", "supply_rate_per_kwh", "kwh_used", "total_cost")
        facts = {k: bill.get(k) for k in keys}
        if savings:
            facts["estimated_yearly_kwh"] = _yearly_kwh(bill)
            facts["app_savings_figures"] = [
                {"title": r["title"], "yearly_savings": r["yearly_savings"]}
                for r in savings["recommendations"]
            ]
        bill_facts = "User's bill facts:\n" + json.dumps(facts) + "\n\n"

    resp = _bedrock.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": ASK_PROMPT.format(
            bill=bill_facts, docs=docs, question=question)}]}],
        inferenceConfig={"maxTokens": 600, "temperature": 0},
    )
    usage = resp["usage"]
    print(f"[ask] {usage['inputTokens']} in / {usage['outputTokens']} out tokens", file=sys.stderr)

    sources = []
    for _, file_name in hits:
        name = SOURCE_NAMES.get(file_name, file_name)
        if name not in sources:
            sources.append(name)
    return {"answer": resp["output"]["message"]["content"][0]["text"], "sources": sources}


if __name__ == "__main__":
    with open(sys.argv[1], "rb") as f:
        bill = read_bill(f.read())
    print(json.dumps(bill, indent=2))
    print(json.dumps(find_savings(bill), indent=2))
