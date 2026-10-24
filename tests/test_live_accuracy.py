"""Accuracy tests against real Bedrock + Knowledge Base. They catch wrong extractions,
missed documents and invented rates or links.

Run:  $env:RUN_LIVE_TESTS="1"; python -m pytest -m live -v
Cost: a few cents per run (about 8 model calls).
"""
import json
import re

import pytest

from conftest import ROOT
from fetch_energyswitch import dollars_per_kwh

import backend

pytestmark = pytest.mark.live

SAMPLE = ROOT / "sample_bills" / "sample_eversource_bill.png"
ENERGY_SWITCH = json.loads((ROOT / "kb-docs" / "raw" / "energyswitchma-02130-residential.json").read_text("utf-8"))
KB_TEXT = "\n".join(p.read_text("utf-8", errors="ignore") for p in (ROOT / "kb-docs" / "upload").iterdir())

# Hand-read from the sample bill image.
SAMPLE_USAGE = [540, 590, 820, 910, 860, 700, 560, 480, 590, 720, 690, 612]
SAMPLE_MONTHS = ["2025-10", "2025-11", "2025-12"] + [f"2026-{m:02d}" for m in range(1, 10)]


def known_rates():
    """Every $/kWh figure that legitimately exists in the source documents or the bill."""
    rates = {0.17323}
    rates |= {float(x) for x in re.findall(r"\$\s?(0\.\d{3,5})", KB_TEXT)}
    rates |= {float(x) / 100 for x in re.findall(r"(\d{1,2}\.\d{1,3})\s?(?:¢|cents)", KB_TEXT)}
    rates |= {r for r in (dollars_per_kwh(p["pricePerUnit"]) for p in ENERGY_SWITCH) if r}
    return {round(r, 4) for r in rates}


def quoted_rates(text):
    """$/kWh figures quoted in model text, as '$0.13644' or '13.644 cents'."""
    found = [float(x) for x in re.findall(r"\$(0\.\d{3,5})", text)]
    found += [float(x) / 100 for x in re.findall(r"(\d{1,2}\.\d{1,3})\s?(?:¢|cents)", text)]
    return {round(r, 4) for r in found}


def assert_no_invented_rates(text):
    invented = quoted_rates(text) - known_rates()
    assert not invented, f"rates not in any source document: {sorted(invented)}\n{text}"


@pytest.fixture(scope="module")
def bill():
    return backend.read_bill(SAMPLE.read_bytes())


@pytest.fixture(scope="module")
def savings(bill):
    return backend.find_savings(bill)


# ---------------------------------------------------------------- bill extraction
def test_read_bill_matches_the_printed_bill(bill):
    assert "eversource" in (bill["utility"] or "").lower()
    assert re.sub(r"\D", "", bill["account_number"] or "") == "12345678901"
    assert bill["billing_start"] == "2026-09-03"
    assert bill["billing_end"] == "2026-10-02"
    assert bill["kwh_used"] == 612
    assert bill["supply_rate_per_kwh"] == pytest.approx(0.17323)
    assert bill["total_cost"] == pytest.approx(219.15)
    assert "basic" in (bill["supplier"] or "").lower()


def test_read_bill_reads_the_usage_chart(bill):
    assert [m["month"] for m in bill["monthly_kwh_history"]] == SAMPLE_MONTHS
    assert [m["kwh"] for m in bill["monthly_kwh_history"]] == SAMPLE_USAGE


# ---------------------------------------------------------------- recommendations
def test_best_supply_switch_is_bcce_basic(savings):
    switches = [r for r in savings["recommendations"] if r["yearly_savings"] > 50]
    assert switches, savings
    best = max(savings["recommendations"], key=lambda r: r["yearly_savings"])
    assert "bcce" in best["title"].lower() or "community choice" in best["title"].lower()
    assert best["yearly_savings"] == pytest.approx((0.17323 - 0.13644) * sum(SAMPLE_USAGE), abs=0.01)


def test_recommendations_quote_only_real_rates(savings):
    for rec in savings["recommendations"]:
        assert_no_invented_rates(rec["title"] + " " + rec["why"])


def test_recommendation_links_come_from_the_documents(savings):
    for rec in savings["recommendations"]:
        if rec["link"]:
            domain = re.sub(r"^https?://(www\.)?", "", rec["link"]).split("/")[0]
            assert domain in KB_TEXT, f"link domain not in any document: {rec['link']}"


# ---------------------------------------------------------------- retrieval + chat
NO_FEE_QUESTION = "Which competitive suppliers have no cancellation fee and a fixed rate below 16 cents per kWh?"


def expected_no_fee_cheap_suppliers():
    """Ground truth from the Energy Switch MA snapshot (excluding the city's BCCE program)."""
    names = set()
    for p in ENERGY_SWITCH:
        r = dollars_per_kwh(p["pricePerUnit"])
        if (p["rowType"] != "DistributionCompany" and p["pricingStructureDescription"] == "Fixed"
                and r and r < 0.16 and not p["hasEarlyTerminationFee"]
                and "Community Choice" not in p["supplierName"]):
            names.add(p["supplierName"].split()[0])  # first word, e.g. "CleanChoice"
    return names


def test_retrieval_finds_the_cheap_no_fee_offers():
    chunks = " ".join(text for text, _ in backend._search_kb(NO_FEE_QUESTION, n=8))
    for name in expected_no_fee_cheap_suppliers():
        assert name in chunks, f"{name} missing from retrieved chunks"


def test_chat_lists_cheap_no_fee_suppliers():
    answer = backend.ask(NO_FEE_QUESTION)["answer"]
    expected = expected_no_fee_cheap_suppliers()
    assert expected, "snapshot has no matching supplier; update the test"
    for name in expected:
        assert name.lower() in answer.lower(), answer
    assert_no_invented_rates(answer)
    # Every rate offered as an answer must actually be below 16 cents
    # (Basic Service at $0.17323 may be quoted as the comparison point).
    too_high = {r for r in quoted_rates(answer) if r >= 0.16} - {0.1732}
    assert not too_high, f"listed offers that are not below 16 cents: {sorted(too_high)}\n{answer}"


@pytest.mark.parametrize("question, must_contain", [
    ("What is the cheapest BCCE plan rate?", ("0.13644", "13.644")),
    ("What is Eversource's current residential Basic Service supply rate in eastern Massachusetts?",
     ("0.17323", "17.323")),
    ("What is National Grid's fixed residential Basic Service rate for August 2026 to January 2027?",
     ("0.17185", "17.185")),
])
def test_chat_quotes_exact_rates(question, must_contain):
    reply = backend.ask(question)
    assert any(s in reply["answer"] for s in must_contain), reply["answer"]
    assert_no_invented_rates(reply["answer"])
    assert reply["sources"]


def test_chat_with_bill_quotes_app_savings(bill, savings):
    reply = backend.ask("How much would I save per year by switching to BCCE Basic?", bill, savings)
    best = max(savings["recommendations"], key=lambda r: r["yearly_savings"])
    # Accept "$296.97" or "$297".
    assert f"{best['yearly_savings']:,.2f}" in reply["answer"] or f"{round(best['yearly_savings']):,}" in reply["answer"], \
        reply["answer"]
