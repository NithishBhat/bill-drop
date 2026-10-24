"""Bill Drop: upload a Massachusetts electricity bill, see how to save.

Run with:  streamlit run app.py
"""
import html
from pathlib import Path

import streamlit as st

from assisted_fill import BCCE_FORM_URL, bcce_plan, bookmarklet_url, form_fields, missing_fields
from backend import ask, find_savings, read_bill

SAMPLE_BILL = Path(__file__).parent / "sample_bills" / "sample_eversource_bill.png"

st.set_page_config(page_title="Bill Drop", page_icon="⚡", layout="centered")


# Cached so Streamlit reruns (every click) don't re-bill us for the same upload.
@st.cache_data(show_spinner=False)
def analyze(file_bytes):
    bill = read_bill(file_bytes)
    return bill, find_savings(bill)


def money(x):
    return "—" if x is None else f"${x:,.0f}"


def drag_button_html(url):
    """The draggable bookmarklet with a 'drag me' hint. Rendered in an iframe so Streamlit doesn't
    strip the javascript: link; bill values are percent-encoded in the URL, so they can't inject HTML.
    The link text becomes the bookmark's name, so the hint sits beside it, not inside it."""
    return f"""<style>
  html,body{{margin:0;overflow:hidden;font:15px sans-serif;color:#fafafa}}
  @media (prefers-color-scheme: light){{body{{color:#262730}}}}
  .row{{display:flex;align-items:center;gap:12px;height:48px}}
  a{{background:#2e7d32;color:#fff;padding:11px 18px;border-radius:8px;font-weight:700;
     text-decoration:none;cursor:grab;white-space:nowrap;box-shadow:0 0 0 3px #a5d6a7}}
  .hint{{opacity:.9}} .arrow{{display:inline-block;animation:up 1s ease-in-out infinite}}
  @keyframes up{{50%{{transform:translateY(-5px)}}}}
</style>
<div class="row">
  <a href="{html.escape(url)}" draggable="true" onclick="
     alert('Almost! Don\\'t click it here. Drag this green button up onto your bookmarks bar '
           + '(press ' + key + ' if you can\\'t see the bar). Then open the BCCE form and click the bookmark there.');
     return false;">⚡ Fill BCCE form</a>
  <span class="hint"><span class="arrow">⬆</span> <b>Drag</b> this up to your bookmarks bar</span>
</div>
<script>var key = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ + Shift + B' : 'Ctrl + Shift + B';</script>"""


def plain(text):
    """Escape $ so Streamlit doesn't render model text like '$250 to $500' as LaTeX math."""
    return text.replace("$", "\\$")


st.session_state.setdefault("result", None)
st.session_state.setdefault("chat", [])

st.title("⚡ Bill Drop")
st.write("Upload your Massachusetts electric bill. We'll read it, find a cheaper supply plan "
         "and the Mass Save rebates that fit your home, and show what you'd save per year.")

# ---------------------------------------------------------------- upload
uploaded = st.file_uploader("Your electric bill (photo, screenshot or PDF)",
                            type=["png", "jpg", "jpeg", "webp", "pdf"])
col_a, col_b = st.columns(2)
run = col_a.button("Analyze my bill", type="primary", disabled=uploaded is None,
                   use_container_width=True)
sample = col_b.button("Try a sample bill", use_container_width=True)

file_bytes = None
if run and uploaded is not None:
    file_bytes = uploaded.getvalue()
elif sample:
    file_bytes = SAMPLE_BILL.read_bytes()

if file_bytes:
    try:
        with st.spinner("Reading your bill and checking Massachusetts rates and rebates..."):
            st.session_state.result = analyze(file_bytes)
        st.session_state.chat = []  # new bill, new conversation
    except ValueError as e:  # unsupported file type
        st.error(str(e))
    except Exception as e:
        st.error(f"Something went wrong analyzing the bill: {e}")

# ---------------------------------------------------------------- results
if st.session_state.result:
    bill, savings = st.session_state.result
    st.divider()
    st.subheader("Your bill")
    st.caption(f"{bill['utility'] or 'Unknown utility'} · Supplier: {bill['supplier'] or 'unknown'} · "
               f"{bill['billing_start'] or '?'} to {bill['billing_end'] or '?'}")

    m1, m2, m3 = st.columns(3)
    rate = bill["supply_rate_per_kwh"]
    m1.metric("Supply rate", f"${rate:.4f}/kWh" if rate else "—")
    m2.metric("Used this bill", f"{bill['kwh_used']:,.0f} kWh" if bill["kwh_used"] else "—")
    m3.metric("Est. yearly cost", money(savings["yearly_cost_now"]))

    history = bill["monthly_kwh_history"]
    if history:
        st.caption("Monthly usage (kWh)")
        st.bar_chart({m["month"]: m["kwh"] for m in history}, height=220)

    st.subheader("How you can save")
    total = savings["total_yearly_savings"]
    if total:
        st.success(f"### You could save about **{money(total)} per year**")

    if not savings["recommendations"]:
        st.info("You're already on a good rate. Check back when rates change in February and August.")
    for rec in savings["recommendations"]:
        with st.container(border=True):
            left, right = st.columns([3, 1])
            left.markdown(f"**{rec['title']}**")
            right.markdown(f"**{money(rec['yearly_savings'])}/yr**" if rec["yearly_savings"]
                           else "_Savings vary_")
            st.markdown(plain(rec["why"]))
            if rec["link"]:
                st.link_button("Learn more / sign up", rec["link"])

    # ------------------------------------------------------------ assisted enrollment
    plan = bcce_plan(savings)
    if plan and "eversource" in (bill["utility"] or "").lower():
        plan_id, plan_name, plan_rate = plan
        fields = form_fields(bill, plan_id)
        st.subheader("✍️ Sign up for BCCE in one click")
        st.write(f"Bill Drop can fill in the City of Boston's sign-up form for **BCCE {plan_name}** "
                 f"(\\${plan_rate}/kWh) using your bill. You just check it and press Submit.")

        with st.container(border=True):
            st.markdown("**Step 1 · Save the helper button** (one time, on a laptop)")
            st.markdown("Press **Ctrl + Shift + B** (Mac: **⌘ + Shift + B**) so your bookmarks bar shows "
                        "at the top of the browser. Then **drag** the green button up onto it.")
            st.iframe(drag_button_html(bookmarklet_url(fields)), height=52)

        with st.container(border=True):
            st.markdown("**Step 2 · Open the form and click your new bookmark**")
            st.link_button("Open the BCCE sign-up form ↗", BCCE_FORM_URL, type="primary")
            st.markdown("On the form page, click **⚡ Fill BCCE form** in your bookmarks bar. "
                        "Your details fill in and turn yellow.")

        with st.container(border=True):
            st.markdown("**Step 3 · Check and submit**")
            st.markdown("Check the yellow fields, add your **email and phone**, then press **Submit**. "
                        "Bill Drop never submits anything for you.")

        missing = missing_fields(fields)
        if missing:
            st.warning("Not on your bill, so you'll type these yourself: " + ", ".join(missing))
        st.caption("Boston residents on Eversource only. The bookmark contains your account details, "
                   "so delete it once you've enrolled.")
        with st.expander("On a phone? Copy the values instead"):
            for f in fields:
                if f["kind"] == "text" and f["value"] and not f["label"].startswith("Re-enter"):
                    st.caption(f["label"])
                    st.code(f["value"], language=None)

    with st.expander("What we read from your bill"):
        def shown(value, fmt="{}"):
            return "Not shown on bill" if value is None else fmt.format(value)

        details = [
            ("Utility", shown(bill["utility"])),
            ("Account number", shown(bill["account_number"])),
            ("Billing period", f"{shown(bill['billing_start'])} to {shown(bill['billing_end'])}"),
            ("Electricity used", shown(bill["kwh_used"], "{:,.0f} kWh")),
            ("Amount due", shown(bill["total_cost"], "${:,.2f}")),
            ("Supply rate", shown(bill["supply_rate_per_kwh"], "${:.5f}/kWh")),
            ("Supplier", shown(bill["supplier"])),
            ("Name on account", shown(bill.get("name_on_account"))),
            ("Service address", shown(", ".join(p for p in (bill.get("service_street"), bill.get("service_city"),
                                                            bill.get("service_zip")) if p) or None)),
            ("Service reference #", shown(bill.get("service_reference_number"))),
            ("Meter number", shown(bill.get("meter_number"))),
            ("Customer name key", shown(bill.get("customer_name_key"))),
        ]
        st.dataframe([{"Item": k, "From your bill": v} for k, v in details],
                     hide_index=True, width="stretch")
        if history:
            st.caption("Monthly usage we read from the chart")
            st.dataframe([{"Month": m["month"], "kWh": m["kwh"]} for m in history],
                         hide_index=True, width="stretch")

# ---------------------------------------------------------------- chat
st.divider()
st.subheader("💬 Ask the coach")
st.caption("Answers come only from Mass Save, utility and City of Boston documents.")

for msg in st.session_state.chat:
    with st.chat_message(msg["role"]):
        st.markdown(plain(msg["content"]))
        if msg.get("sources"):
            st.caption("Sources: " + ", ".join(msg["sources"]))

question = st.chat_input("e.g. What rebates can I get for a heat pump?")
if question:
    st.session_state.chat.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(plain(question))
    with st.chat_message("assistant"):
        try:
            with st.spinner("Looking it up..."):
                bill, savings = st.session_state.result or (None, None)
                reply = ask(question, bill, savings)
        except Exception as e:
            reply = {"answer": f"Sorry, I couldn't answer that right now ({e}).", "sources": []}
        st.markdown(plain(reply["answer"]))
        if reply["sources"]:
            st.caption("Sources: " + ", ".join(reply["sources"]))
    st.session_state.chat.append({"role": "assistant", "content": reply["answer"],
                                  "sources": reply["sources"]})
