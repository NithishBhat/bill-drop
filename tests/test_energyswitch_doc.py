"""Offline checks that the Energy Switch MA Knowledge Base docs match the state's data exactly."""
import json
import re

import pytest

from conftest import ROOT
from fetch_energyswitch import build_docs, dollars_per_kwh

RAW = ROOT / "kb-docs" / "raw" / "energyswitchma-02130-residential.json"
UPLOAD = ROOT / "kb-docs" / "upload"
TOPICS = ("lowest-rates", "no-cancellation-fee", "100-percent-renewable")

# Knowledge Base default chunking is ~300 tokens; stay under it so each topic list is one chunk.
MAX_TOPIC_CHARS = 1100


@pytest.fixture(scope="module")
def plans():
    return json.loads(RAW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def docs():
    return {p.name: p.read_text(encoding="utf-8") for p in UPLOAD.glob("energyswitchma-02130-*.md")}


def offers(plans):
    return [p for p in plans if p["rowType"] != "DistributionCompany"]


def name(p):
    return re.sub(r"\s+", " ", p["supplierName"]).strip()


def listed(doc):
    """[(supplier, rate)] in the order they appear in a topic file."""
    return [(n, float(r)) for n, r in re.findall(r"^- (.+?): \$(0\.\d+)/kWh", doc, re.M)]


@pytest.mark.parametrize("text, expected", [
    ("14.214 ¢/kWh", 0.14214), ("17.323 ¢/kWh|TBD", 0.17323), ("15.1 ¢/kWh", 0.151), ("TBD", None), (None, None),
])
def test_cents_to_dollars(text, expected):
    assert dollars_per_kwh(text) == expected


def test_committed_docs_match_snapshot(plans, docs):
    strip = lambda t: re.sub(r"retrieved \d{4}-\d{2}-\d{2}", "", t)
    built = build_docs(plans, "02130")
    assert set(built) == set(docs)
    for file_name, text in built.items():
        assert strip(text) == strip(docs[file_name]), file_name


def test_all_offers_doc_has_every_offer_with_exact_rate(plans, docs):
    doc = docs["energyswitchma-02130-all-offers.md"]
    for p in plans:
        rate = dollars_per_kwh(p["pricePerUnit"])
        if rate is not None:
            assert f"- {name(p)} (Energy Switch MA offer): supply rate ${rate}/kWh" in doc, name(p)


@pytest.mark.parametrize("topic", TOPICS)
def test_topic_files_fit_in_one_chunk(docs, topic):
    assert len(docs[f"energyswitchma-02130-{topic}.md"]) <= MAX_TOPIC_CHARS


@pytest.mark.parametrize("topic", TOPICS)
def test_topic_lists_are_sorted_cheapest_first(docs, topic):
    rates = [r for _, r in listed(docs[f"energyswitchma-02130-{topic}.md"])]
    assert rates == sorted(rates)


def test_lowest_rates_are_the_true_lowest(plans, docs):
    expected = sorted(dollars_per_kwh(p["pricePerUnit"]) for p in offers(plans) if dollars_per_kwh(p["pricePerUnit"]))
    got = [r for _, r in listed(docs["energyswitchma-02130-lowest-rates.md"])]
    assert got == expected[:len(got)]


def test_no_fee_list_only_has_no_fee_fixed_offers(plans, docs):
    no_fee = {(name(p), dollars_per_kwh(p["pricePerUnit"])) for p in offers(plans)
              if not p["hasEarlyTerminationFee"] and p["pricingStructureDescription"] == "Fixed"}
    got = listed(docs["energyswitchma-02130-no-cancellation-fee.md"])
    assert got and all(row in no_fee for row in got)
    # And none of the cheapest qualifying offers were dropped.
    assert [r for _, r in got] == sorted(r for _, r in no_fee)[:len(got)]


def test_renewable_list_only_has_100_percent_offers(plans, docs):
    green = {(name(p), dollars_per_kwh(p["pricePerUnit"])) for p in offers(plans)
             if p["renewableEnergyProductPercentage"] >= 100}
    got = listed(docs["energyswitchma-02130-100-percent-renewable.md"])
    assert got and all(row in green for row in got)


def test_detail_lines_are_self_contained(docs):
    lines = [l for l in docs["energyswitchma-02130-all-offers.md"].splitlines() if l.startswith("- ")]
    assert lines
    for line in lines:
        assert "(Energy Switch MA offer)" in line and len(line) < 800
        assert "utm_" not in line
