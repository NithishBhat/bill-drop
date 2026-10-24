"""Assisted enrollment for the Boston Community Choice Electricity (BCCE) sign-up form.

The app can't type into another website, so we build a bookmarklet: a bookmark whose URL is
JavaScript. The user drags it to their bookmarks bar, opens the BCCE form and clicks it. It
fills the fields we read from their bill and highlights them. It never submits the form or
touches the reCAPTCHA: the user checks everything, adds email and phone, and submits.

Field ids/names come from the live form (Gravity Forms form 13) at BCCE_FORM_URL.
"""
import json
from urllib.parse import quote

BCCE_FORM_URL = "https://www.cityofbostoncce.com/form/"

# BCCE plan rate ($/kWh, locked Dec 2025 - Dec 2027) -> (radio id, plan name)
BCCE_PLANS = {
    0.13644: ("choice_13_26_1", "Optional Basic"),
    0.14214: ("choice_13_26_0", "Standard"),
    0.16231: ("choice_13_26_2", "Optional Green 100"),
}


def bcce_plan(savings):
    """Return (radio_id, plan_name, rate) for the recommended BCCE plan, or None."""
    for rec in savings["recommendations"]:
        rate = rec.get("new_supply_rate_per_kwh")
        for plan_rate, (radio_id, name) in BCCE_PLANS.items():
            if rate is not None and abs(rate - plan_rate) < 0.00001:
                return radio_id, name, plan_rate
    return None


def _digits(value):
    """'1234 567 8901' -> '12345678901'. The form caps account (11) and service ref (8) length."""
    digits = "".join(c for c in value or "" if c.isdigit())
    return digits or None


def form_fields(bill, plan_radio_id):
    """Every field we can fill, in form order. kind: click (radio/checkbox), text, select."""
    account = _digits(bill.get("account_number"))
    service_ref = _digits(bill.get("service_reference_number"))
    already_bcce = "city of boston" in (bill.get("supplier") or "").lower() or \
                   "community choice" in (bill.get("supplier") or "").lower()
    fields = [
        ("Utility: Eversource East", "click", "choice_13_32_0", "Eversource East"),
        # Already on BCCE -> just change plan; otherwise opt in.
        ("Action", "click", "choice_13_4_2" if already_bcce else "choice_13_4_0",
         "Change Product Selection" if already_bcce else "OPT-IN"),
        ("Customer type", "click", "choice_13_5_0", "Residential"),
        ("Plan", "click", plan_radio_id, None),
        ("Account number", "text", "input_23.1", account),
        ("Re-enter account number", "text", "input_23.2", account),
        ("Service reference #", "text", "input_39.1", service_ref),
        ("Re-enter service reference #", "text", "input_39.2", service_ref),
        ("Meter number", "text", "input_31.1", bill.get("meter_number")),
        ("Re-enter meter number", "text", "input_31.2", bill.get("meter_number")),
        ("Customer name key", "text", "input_27", bill.get("customer_name_key")),
        ("Name on account", "text", "input_11", bill.get("name_on_account")),
        ("Service street address", "text", "input_12.1", bill.get("service_street")),
        ("Service city", "text", "input_12.3", bill.get("service_city")),
        ("Service state", "select", "input_12.4", "MA"),
        ("Service ZIP", "text", "input_12.5", bill.get("service_zip")),
        # Clicked last, after the address is filled, so the form copies it to mailing.
        ("Mailing address same as service", "click", "choice_13_35_1", "Yes"),
    ]
    plan_name = next((n for r, n in BCCE_PLANS.values() if r == plan_radio_id), "")
    return [{"label": label, "kind": kind, "target": target,
             "value": plan_name if label == "Plan" else value}
            for label, kind, target, value in fields]


def missing_fields(fields):
    """Labels of text fields the bill didn't give us (the user types these)."""
    return [f["label"] for f in fields if f["kind"] == "text" and not f["value"]
            and not f["label"].startswith("Re-enter")]


# %s is replaced by the JSON field list. Radios first (they reveal conditional fields),
# then text after a short delay, then the "mailing same" checkbox. Fields the form has
# hidden or disabled itself (e.g. the single-option utility, meter # on opt-in) are skipped.
# Never submits.
_JS = """(()=>{
if(!/cityofbostoncce\\.com$/.test(location.hostname)){alert('Open the BCCE enrollment form first: %(url)s');return}
const F=%(fields)s,miss=[],long=[];let n=0;
const mark=e=>{e.style.outline='3px solid #f5a623';e.style.outlineOffset='2px';e.style.background='#fff8e1'};
const set=(e,v)=>{const d=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(e),'value');
 d&&d.set?d.set.call(e,v):e.value=v;['input','change','keyup','blur'].forEach(t=>e.dispatchEvent(new Event(t,{bubbles:true})))};
const off=e=>e.disabled||!e.offsetParent;
const click=f=>{const e=document.getElementById(f.target);if(!e){miss.push(f.label);return}
 if(off(e))return;if(!e.checked)e.click();mark(e.closest('.gfield')||e);n++};
F.filter(f=>f.kind==='click'&&f.target!=='choice_13_35_1').forEach(click);
setTimeout(()=>{
 F.filter(f=>f.kind!=='click'&&f.value).forEach(f=>{const e=document.querySelector('[name="'+f.target+'"]');
  if(!e){miss.push(f.label);return}if(off(e))return;
  if(e.maxLength>0&&f.value.length>e.maxLength)long.push(f.label);set(e,f.value);mark(e);n++});
 F.filter(f=>f.target==='choice_13_35_1').forEach(click);
 const b=document.createElement('div');
 b.style.cssText='position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#1b5e20;color:#fff;padding:12px 44px 12px 16px;font:16px/1.4 sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.3)';
 b.textContent='Bill Drop filled '+n+' fields from your bill. Check every highlighted field, add your email and phone, then click Submit yourself.'+(miss.length?' Not found on the form: '+miss.join(', ')+'.':'')
  +(long.length?' Longer than the form allows, please fix: '+long.join(', ')+'.':'');
 const x=document.createElement('button');x.textContent='\\u00d7';x.setAttribute('aria-label','Close');
 x.style.cssText='position:absolute;right:12px;top:6px;background:none;border:0;color:#fff;font-size:24px;cursor:pointer';
 x.onclick=()=>b.remove();b.appendChild(x);document.body.appendChild(b);
 const em=document.querySelector('[name="input_13"]');if(em){mark(em);em.scrollIntoView({block:'center'});em.focus()}
},500)})()"""


def bookmarklet_js(fields):
    """The bookmarklet's JavaScript. Bill values are JSON-encoded, so quotes or '</script>'
    in a name or address can't break out of the string."""
    data = [f for f in fields if f["kind"] != "click" or f["target"]]
    payload = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c")
    return _JS % {"fields": payload, "url": BCCE_FORM_URL}


def bookmarklet_url(fields):
    """javascript: URL. Fully percent-encoded, since browsers decode % in bookmarklets."""
    return "javascript:" + quote(bookmarklet_js(fields), safe="")
