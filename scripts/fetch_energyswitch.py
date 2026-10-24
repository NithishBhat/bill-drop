"""Pull electricity supply offers from the state's Energy Switch MA site into a Knowledge Base doc.

Source: https://www.energyswitchma.gov/#/compare/2/1/02130//
  (distribution company 2 = Eversource/NSTAR, customer class 1 = Residential, zip 02130)

Usage:  python scripts/fetch_energyswitch.py [zip] [distribution_company_id]
Writes: kb-docs/upload/energyswitchma-<zip>-residential.md  (Knowledge Base doc)
        kb-docs/raw/energyswitchma-<zip>-residential.json   (raw snapshot, used as test ground truth)

The Knowledge Base splits docs into ~300-token chunks, so every line here is self-contained
(supplier name + rate + key terms on one short line) to keep facts together after chunking.
"""
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

API = "https://www.energyswitchma.gov/consumers/compare"
MONTHLY_KWH = 600  # the site's default for homes; estimatedCost is based on this


def fetch(zip_code, company_id):
    body = json.dumps({"customerClassId": 1, "distributionCompanyId": company_id,
                       "zipCode": zip_code, "distributionCompanyTownName": None,
                       "monthlyUsage": MONTHLY_KWH}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def dollars_per_kwh(price):
    """'14.214 ¢/kWh' -> 0.14214. Returns None for prices that aren't a plain ¢/kWh number."""
    m = re.match(r"\s*([\d.]+)\s*¢/kWh", price or "")
    return round(float(m.group(1)) / 100, 5) if m else None


def clean(text):
    return re.sub(r"\s+", " ", (text or "").replace("|", "; ")).strip()


def short_link(url):
    """Drop tracking query strings so links don't eat chunk space."""
    if not url:
        return None
    parts = urlsplit(url if "://" in url else "https://" + url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}".rstrip("/")


def cancellation(p):
    return clean(p["earlyTerminationDetail"]) or "No cancellation fee"


def summary_line(p):
    """One compact, self-contained line per offer (name, rate, pricing, term, fee)."""
    rate = dollars_per_kwh(p["pricePerUnit"])
    price = f"${rate}/kWh" if rate else clean(p["pricePerUnit"])
    extra = "; new customers only" if p["isNewCustomerOnly"] else ""
    return (f"- {clean(p['supplierName'])}: {price}, {clean(p['pricingStructureDescription']) or 'utility default'}, "
            f"term {clean(p['contractTerm']) or 'n/a'}, {cancellation(p).lower()}{extra}.")


def plan_line(p):
    rate = dollars_per_kwh(p["pricePerUnit"])
    name = clean(p["supplierName"])
    parts = [
        f"supply rate ${rate}/kWh" if rate else f"supply price {clean(p['pricePerUnit'])}",
        f"pricing {clean(p['pricingStructureDescription']) or 'utility default'}",
        f"term {clean(p['contractTerm']) or 'n/a'}",
        f"monthly fee {clean(p['pricePerMonth']) or 'none'}",
        f"enrollment fee {clean(p['enrollmentFee']) or 'none'}",
        cancellation(p),
        clean(p["automaticRenewalDetail"]) or "no automatic renewal",
        f"renewable content {p['renewableEnergyProduct'] or '0%'}",
        "new customers only" if p["isNewCustomerOnly"] else "open to existing customers",
        f"est. {p['estimatedCostExport']}/month at {MONTHLY_KWH} kWh",
    ]
    if p["introductoryPrice"]:
        parts.append(f"introductory price {clean(p['introductoryPrice'])}")
    link = short_link(p["productWebsiteUrl"] or p["supplierWebsiteUrl"])
    if link:
        parts.append(f"link {link}")
    if p["isMunicipalAggregationDefault"]:
        parts.append(f"municipal aggregation default for {p['municipalAggregationTown']}")
    return f"- {name} (Energy Switch MA offer): " + "; ".join(parts) + "."


def rate_of(p):
    return dollars_per_kwh(p["pricePerUnit"]) or 1e9


def build_docs(plans, zip_code):
    """Return {file_name: markdown}. Small topic files each fit in one KB chunk, so a search
    hit on a topic ("no cancellation fee", "cheapest") returns the whole, correctly sorted list."""
    basic = [p for p in plans if p["rowType"] == "DistributionCompany"]
    offers = sorted((p for p in plans if p["rowType"] != "DistributionCompany"),
                    key=lambda p: (rate_of(p), p["estimatedCost"] or 1e9))
    company = clean(basic[0]["supplierName"]) if basic else "the distribution company"
    scope = f"Eversource (NSTAR) residential customers, zip {zip_code}"
    source = f"Source: Energy Switch MA (MA DPU, energyswitchma.gov), retrieved {date.today():%Y-%m-%d}."
    basic_line = f"- Eversource Basic Service: ${dollars_per_kwh(basic[0]['pricePerUnit'])}/kWh" if basic else ""

    no_fee = [p for p in offers if not p["hasEarlyTerminationFee"]
              and p["pricingStructureDescription"] == "Fixed"]
    green = [p for p in offers if p["renewableEnergyProductPercentage"] >= 100]
    prefix = f"energyswitchma-{zip_code}"

    def topic(title, rows):
        # Kept under ~1,100 characters so the whole list lands in a single ~300-token chunk.
        return "\n".join([f"# {title} ({scope})", source,
                          f"For comparison, utility default {basic_line[2:]}.", "",
                          *[summary_line(p) for p in rows], ""])

    return {
        f"{prefix}-lowest-rates.md": topic(
            "Lowest electricity supply rates on Energy Switch MA", offers[:7]),
        f"{prefix}-no-cancellation-fee.md": topic(
            "Fixed-rate supplier offers with no cancellation (early termination) fee, cheapest first",
            no_fee[:7]),
        f"{prefix}-100-percent-renewable.md": topic(
            "100% renewable electricity supply offers, cheapest first", green[:7]),
        f"{prefix}-all-offers.md": "\n".join([
            f"# Energy Switch MA: all residential electricity supply offers ({scope})",
            source,
            f"Prices are supply (generation) rates only; delivery charges from {company} are the same "
            "whichever supplier you pick. Rates converted from cents to dollars per kWh. "
            f"Estimated monthly costs assume {MONTHLY_KWH} kWh/month. Competitive offers can carry "
            "cancellation fees, automatic renewal at a different price, or introductory rates.",
            "",
            *[plan_line(p) for p in basic + offers],
            "",
        ]),
    }


if __name__ == "__main__":
    zip_code = sys.argv[1] if len(sys.argv) > 1 else "02130"
    company_id = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    plans = fetch(zip_code, company_id)
    root = Path(__file__).resolve().parent.parent / "kb-docs"
    (root / "raw" / f"energyswitchma-{zip_code}-residential.json").write_text(
        json.dumps(plans, indent=1, ensure_ascii=False), encoding="utf-8")
    for old in (root / "upload").glob(f"energyswitchma-{zip_code}-*.md"):
        old.unlink()
    for name, text in build_docs(plans, zip_code).items():
        (root / "upload" / name).write_text(text, encoding="utf-8")
        print(f"{name}: {len(text)} chars")
