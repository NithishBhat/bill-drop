"""Tests for the BCCE assisted-enrollment bookmarklet (no AWS needed)."""
import json
import shutil
import subprocess
from urllib.parse import unquote

import pytest
from hypothesis import given, settings, strategies as st

from assisted_fill import bcce_plan, bookmarklet_js, bookmarklet_url, form_fields, missing_fields

BILL = {
    "utility": "Eversource", "supplier": "Basic Service", "account_number": "1234 567 8901",
    "service_reference_number": "98765432", "meter_number": "M123456789", "customer_name_key": "SAMP",
    "name_on_account": "JANE SAMPLE", "service_street": "110 Example St", "service_city": "Boston",
    "service_zip": "02115",
}


def payload(js):
    """The JSON field list embedded in the bookmarklet."""
    start = js.index("const F=") + len("const F=")
    return json.loads(js[start:js.index(",miss=[]")])


def test_picks_bcce_plan_by_rate():
    savings = {"recommendations": [
        {"title": "Heat pumps", "new_supply_rate_per_kwh": None},
        {"title": "Switch to BCCE Basic", "new_supply_rate_per_kwh": 0.13644},
    ]}
    assert bcce_plan(savings) == ("choice_13_26_1", "Optional Basic", 0.13644)


def test_no_plan_for_non_bcce_switch():
    savings = {"recommendations": [{"title": "Switch to Supplier X", "new_supply_rate_per_kwh": 0.129}]}
    assert bcce_plan(savings) is None


def test_opt_in_vs_change_plan():
    action = lambda bill: next(f for f in form_fields(bill, "choice_13_26_1") if f["label"] == "Action")
    assert action(BILL)["target"] == "choice_13_4_0"  # OPT-IN
    on_bcce = dict(BILL, supplier="Billing for City of Boston Community Choice Electricity")
    assert action(on_bcce)["target"] == "choice_13_4_2"  # Change Product Selection


def test_missing_values_are_skipped_not_filled_as_none():
    bill = dict(BILL, meter_number=None, customer_name_key=None)
    fields = form_fields(bill, "choice_13_26_1")
    assert missing_fields(fields) == ["Meter number", "Customer name key"]
    filled = [f for f in payload(bookmarklet_js(fields)) if f["kind"] != "click"]
    assert all(f["value"] for f in filled if f["target"] not in ("input_31.1", "input_31.2", "input_27"))
    assert "None" not in bookmarklet_js(fields)


def test_account_and_service_ref_fit_form_limits():
    """The form caps account at 11 chars and service reference at 8; bills print them with spaces."""
    fields = {f["target"]: f["value"] for f in form_fields(BILL, "choice_13_26_1")}
    assert fields["input_23.1"] == fields["input_23.2"] == "12345678901"
    assert fields["input_39.1"] == "98765432"


def test_never_submits():
    js = bookmarklet_js(form_fields(BILL, "choice_13_26_1"))
    assert "submit" not in js.replace("Submit yourself", "").lower()
    assert "recaptcha" not in js.lower()


@settings(max_examples=200)
@given(st.text(min_size=1, max_size=40))
def test_any_bill_text_round_trips_safely(value):
    """Quotes, '</script>', '%' and unicode in bill values stay data, never code."""
    bill = dict(BILL, name_on_account=value, service_street=value)
    fields = form_fields(bill, "choice_13_26_1")
    js = bookmarklet_js(fields)
    url = bookmarklet_url(fields)
    assert url.startswith("javascript:")
    assert unquote(url[len("javascript:"):]) == js
    data = payload(js)
    assert next(f for f in data if f["target"] == "input_11")["value"] == value
    assert "<" not in js[js.index("const F="):js.index(",miss=[]")]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_bookmarklet_is_valid_javascript(tmp_path):
    bill = dict(BILL, name_on_account="O'Brien \"Jr\" </script> 100%")
    js = bookmarklet_js(form_fields(bill, "choice_13_26_1"))
    path = tmp_path / "bm.js"
    path.write_text(js, encoding="utf-8")
    subprocess.run(["node", "--check", str(path)], check=True)
