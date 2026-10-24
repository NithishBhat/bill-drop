# MassEnergy Kiro Steering Rules

Energy Bill Coach: a Massachusetts household uploads an electricity bill and sees cheaper supply
plans and Mass Save rebates. These rules are architectural boundaries. Follow them on every change;
if a request conflicts with one, say so instead of silently breaking it.

## 1. Frontend: Streamlit only
- All UI lives in `app.py` and uses Streamlit native widgets (`st.file_uploader`, `st.metric`,
  `st.bar_chart`, `st.chat_input`, `st.container`, ...). No React, Flask, custom JS/HTML components,
  or `unsafe_allow_html=True`.
- `app.py` never calls boto3 or Bedrock directly. It only imports from `backend.py`.
- Wrap backend calls in `@st.cache_data` so reruns don't re-bill Bedrock. Never cache a fallback
  result (`analyze.clear(file_bytes)` when `is_fallback` is True).
- Pass all model text through `plain()` before `st.markdown` so `$` isn't rendered as LaTeX.

## 2. Backend: AWS Bedrock via boto3 only
- All LLM calls go through the boto3 `bedrock-runtime` client's `converse()`. Knowledge Base
  lookups go through the boto3 `bedrock-agent-runtime` client's `retrieve()`. Region `us-east-1`.
  No other LLM SDKs (openai, anthropic, langchain, ...).
- Structured output is forced through a tool call (`toolChoice` = the one tool) whose `inputSchema`
  is a strict JSON schema with every key listed in `required` (`BILL_SCHEMA`, `SAVINGS_SCHEMA`).
  Use `temperature: 0`. Never parse free-form model text as JSON.
- Python does the money math (savings from rate differences); the model never invents rates,
  rebate amounts or links that aren't in the Knowledge Base documents.
- Model ID comes from `BILL_MODEL_ID`. Credentials come from the standard AWS chain; never put
  keys in code, `.streamlit/secrets.toml` or `.env` in git.

## 3. Failsafe: always return the hardcoded fallback dictionary
- `analyze_bill(file_bytes)` is the only entry point the UI uses for bill analysis, and it never
  raises. Any exception (Bedrock outage or throttling, missing credentials, unsupported upload,
  malformed model output) returns:
  `{"bill": FALLBACK_BILL, "savings": FALLBACK_SAVINGS, "is_fallback": True, "error": "<why>"}`.
- `FALLBACK_BILL` and `FALLBACK_SAVINGS` are hardcoded literal dicts in `backend.py`, returned as
  `copy.deepcopy` so session-state mutations can't corrupt them. They contain no made-up bill
  numbers or savings: every bill field is `None`, savings are 0, and the single recommendation is
  the free Mass Save assessment.
- The UI shows `st.warning` whenever `is_fallback` is True. Never present fallback data as real.
- Logging inside the fallback path must not be able to raise (use `ascii()`; Windows consoles
  are cp1252).

## 4. JSON contract (don't rename keys without updating both sides)
- `analyze_bill` -> `bill`, `savings`, `is_fallback`, `error`
- bill -> `BILL_SCHEMA["required"]`
- `find_savings` -> `yearly_cost_now`, `total_yearly_savings`, `recommendations`
- each recommendation -> `title`, `why`, `yearly_savings`, `link`
- `ask` -> `answer`, `sources`
- Return dict literals so keys stay explicit. `.kiro/hooks/verify_contract.py` checks all of this
  (statically and at runtime) after every save of `backend.py`/`app.py` and every Kiro edit.
  Fix any violation it reports before moving on.

## 5. Testing
- `test_backend.py` uses pytest + Hypothesis property-based tests with fake Bedrock clients.
  Tests never call real AWS.
- Any new backend code path needs a property in `test_backend.py`, including a "well-formed input
  is not masked by the fallback" property so the catch-all can't hide real bugs.
- Run: `python -m pytest test_backend.py -q` (stress: set `HYPOTHESIS_PROFILE=stress`).
