"""Property-based tests for the savings math in backend.find_savings / _yearly_kwh.

Bedrock and the Knowledge Base are replaced with in-process fakes, so these run offline.
"""
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

import backend

# The fake_backend fixture is safe to share across examples: run() overwrites its state every call.
SETTINGS = settings(max_examples=200, deadline=None,
                    suppress_health_check=[HealthCheck.function_scoped_fixture])

money = st.floats(min_value=0, max_value=5000, allow_nan=False)
rate = st.floats(min_value=0.01, max_value=0.60, allow_nan=False)
kwh = st.floats(min_value=0, max_value=5000, allow_nan=False)


@st.composite
def history(draw):
    n = draw(st.integers(min_value=0, max_value=15))
    start = draw(st.integers(min_value=0, max_value=11))
    return [{"month": f"2025-{(start + i) % 12 + 1:02d}", "kwh": draw(kwh)} for i in range(n)]


@st.composite
def bills(draw):
    return {
        "utility": draw(st.sampled_from(["Eversource", "National Grid", None])),
        "supplier": "Basic Service",
        "kwh_used": draw(st.none() | kwh),
        "total_cost": draw(st.none() | money),
        "supply_rate_per_kwh": draw(st.none() | rate),
        "monthly_kwh_history": draw(history()),
    }


@st.composite
def ideas(draw):
    out = []
    for i in range(draw(st.integers(min_value=0, max_value=6))):
        kind = draw(st.sampled_from(["supply_switch", "rebate"]))
        out.append({
            "kind": kind,
            "title": f"{kind}-{i}",
            "why": "test",
            "new_supply_rate_per_kwh": draw(st.none() | rate) if kind == "supply_switch" else None,
            # Includes negative estimates, which must never surface as negative savings.
            "estimated_yearly_savings": draw(st.none() | st.floats(-1000, 3000, allow_nan=False))
            if kind == "rebate" else None,
            "link": draw(st.none() | st.just("www.masssave.com")),
        })
    return out


@pytest.fixture
def fake_backend(monkeypatch):
    """Point find_savings at canned model output instead of Bedrock."""
    state = {}
    monkeypatch.setattr(backend, "_search_kb", lambda query, n=4: [])
    monkeypatch.setattr(backend, "_ask_with_tool",
                        lambda *a, **k: {"recommendations": state["ideas"]})
    return state


def run(fake_backend, bill, idea_list):
    fake_backend["ideas"] = idea_list
    return backend.find_savings(bill)


@SETTINGS
@given(bill=bills(), idea_list=ideas())
def test_savings_are_never_negative(fake_backend, bill, idea_list):
    result = run(fake_backend, bill, idea_list)
    assert all(r["yearly_savings"] >= 0 for r in result["recommendations"])
    assert result["total_yearly_savings"] >= 0


@SETTINGS
@given(bill=bills(), idea_list=ideas())
def test_total_is_sum_of_recommendations(fake_backend, bill, idea_list):
    result = run(fake_backend, bill, idea_list)
    assert result["total_yearly_savings"] == round(sum(r["yearly_savings"] for r in result["recommendations"]), 2)


@SETTINGS
@given(bill=bills(), idea_list=ideas())
def test_only_the_cheapest_supply_switch_is_counted(fake_backend, bill, idea_list):
    result = run(fake_backend, bill, idea_list)
    switches = [r for r in result["recommendations"] if r["title"].startswith("supply_switch")]
    current = bill["supply_rate_per_kwh"]
    cheaper = [i["new_supply_rate_per_kwh"] for i in idea_list if i["kind"] == "supply_switch"
               and i["new_supply_rate_per_kwh"] and current and i["new_supply_rate_per_kwh"] < current]

    # You can only be on one supply plan: never more than one switch counted.
    assert len(switches) == (1 if cheaper else 0)
    if cheaper:
        best = round((current - min(cheaper)) * backend._yearly_kwh(bill), 2)
        assert switches[0]["yearly_savings"] == best


@SETTINGS
@given(bill=bills())
def test_yearly_kwh_uses_12_month_history_when_present(bill):
    hist = bill["monthly_kwh_history"]
    if len(hist) >= 12:
        expected = sum(m["kwh"] for m in hist[-12:])
    else:
        expected = (bill["kwh_used"] or 0) * 12
    assert backend._yearly_kwh(bill) == pytest.approx(expected)


def test_sample_bill_bcce_switch_savings(fake_backend):
    """Hand-checked against sample_bills/sample_eversource_bill.png."""
    usage = [540, 590, 820, 910, 860, 700, 560, 480, 590, 720, 690, 612]
    months = [f"2025-{m}" for m in ("10", "11", "12")] + [f"2026-{m:02d}" for m in range(1, 10)]
    bill = {"utility": "Eversource", "supplier": "Basic Service", "kwh_used": 612,
            "total_cost": 219.15, "supply_rate_per_kwh": 0.17323,
            "monthly_kwh_history": [{"month": m, "kwh": k} for m, k in zip(months, usage)]}
    idea_list = [
        {"kind": "supply_switch", "title": "supply_switch-standard", "why": "", "link": None,
         "new_supply_rate_per_kwh": 0.14214, "estimated_yearly_savings": None},
        {"kind": "supply_switch", "title": "supply_switch-basic", "why": "", "link": None,
         "new_supply_rate_per_kwh": 0.13644, "estimated_yearly_savings": None},
    ]
    result = run(fake_backend, bill, idea_list)
    assert backend._yearly_kwh(bill) == 8072
    assert [r["title"] for r in result["recommendations"]] == ["supply_switch-basic"]
    assert result["recommendations"][0]["yearly_savings"] == 296.97  # (0.17323 - 0.13644) * 8072
