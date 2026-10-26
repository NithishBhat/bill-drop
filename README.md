# ⚡ Bill Drop

**Want to try it?** The hosted demo has been shut down to save costs, but it can be redeployed to AWS
in about 5 minutes with one `terraform apply` (see [Deploy](#deploy)). If you're a recruiter and would
like a live demo, reach out through my GitHub profile and I'll spin it up. Once it's running, click
**"Try a sample bill"** to see it work without uploading anything.

Bill Drop reads a Massachusetts electricity bill, finds a cheaper supply plan and the Mass Save
rebates that fit the home, shows the yearly savings, and fills in the City of Boston's sign-up form
for you. Built at **Hackachusetts 2026** (Climate Tech track).

## The problem
Most Massachusetts households stay on their utility's default "Basic Service" supply rate without
knowing cheaper options exist. In Boston, the City's own Community Choice Electricity (BCCE) plan
was about $0.137/kWh vs. Eversource's $0.173/kWh in late 2026, which works out to about **$265–$300/year**
for a home using 600–675 kWh a month. Finding the right plan, the right rebates, and the 8+ account numbers each sign-up form
asks for takes more effort than most people will spend.

## What it does
1. **Reads your bill.** Upload a photo, screenshot or PDF. Claude (on Amazon Bedrock) extracts the
   supply rate, usage, 12-month history, account number, service reference, meter number and address.
2. **Finds savings.** It searches a Knowledge Base of real Massachusetts documents (Eversource and
   National Grid rate sheets, the City of Boston BCCE plans, Mass Save rebates, and supplier offers
   from the state's Energy Switch MA site) and recommends a cheaper plan plus rebates that fit your
   usage pattern (for example, high winter usage points to heat pumps and insulation).
   **The dollar math is done in Python, not by the model**, so savings figures can't be invented.
3. **Fills the sign-up form for you.** For Boston Eversource customers, Bill Drop builds a one-click
   bookmark that fills the official BCCE enrollment form with your bill details and highlights every
   field. **You check it and press Submit yourself.** Bill Drop never submits forms or touches the reCAPTCHA.
4. **Answers questions.** A chat answers questions like "Can I get a heat pump rebate as a renter?"
   using only the Knowledge Base documents, and lists its sources.

## How to use the sign-up helper
1. Press **Ctrl + Shift + B** (Mac: **⌘ + Shift + B**) to show your bookmarks bar, then **drag** the
   green **⚡ Fill BCCE form** button onto it.
2. Click **Open the BCCE sign-up form**, then click the **⚡ Fill BCCE form** bookmark on that page.
3. Check the highlighted fields, add your email and phone, and submit.

Browsers don't let one website fill another website's form, which is why this is a bookmark you
click on the BCCE page itself. With a pre-fill link from the City, this could become a single click.

## Architecture
```
Browser ──► Streamlit app (app.py) on EC2 (Docker)
               │
               ├─ backend.py ── Bedrock converse() ── Claude Sonnet 4.5  (read bill, recommend, chat)
               │            └── Bedrock Knowledge Base retrieve()        (Titan Text Embeddings V2,
               │                                                          S3 Vectors, docs in kb-docs/upload)
               └─ assisted_fill.py ── builds the BCCE form-fill bookmarklet
```
- **Structured output:** the model answers through a forced tool call with a JSON schema, so the
  frontend always gets the exact keys it expects.
- **Deployment:** Terraform in `infra/` (EC2 c7i-flex.large, least-privilege IAM role limited to
  Claude + this Knowledge Base, SSM instead of SSH, IMDSv2, encrypted disk, Elastic IP).

## Run it locally
Requirements: Python 3.13, an AWS account with Bedrock access to Claude Sonnet 4.5 in `us-east-1`.
```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # macOS/Linux: .venv/bin/pip
aws login                                          # or any AWS credentials
.venv/Scripts/streamlit run app.py
```
- **Model:** set `BILL_MODEL_ID` to use another Bedrock model (e.g. `us.amazon.nova-pro-v1:0`).
- **Your own Knowledge Base:** upload `kb-docs/upload/` to an S3 bucket, create a Bedrock Knowledge
  Base on it (Titan Text Embeddings V2, S3 Vectors), sync it, and set `KNOWLEDGE_BASE_ID` in `backend.py`.
- **Refresh supplier offers:** `python scripts/fetch_energyswitch.py 02130`

## Tests
```bash
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest                    # offline, no AWS needed
RUN_LIVE_TESTS=1 .venv/Scripts/python -m pytest -m live   # real Bedrock calls, a few cents
```
Includes **property-based tests (Hypothesis)** for the savings math (never negative, only the cheapest
plan switch counted, 12-month usage used when available) and for the form-fill bookmark (names or
addresses containing quotes, `</script>` or `%` can't break or inject into the script).

## Deploy
```bash
cd infra
eval "$(aws configure export-credentials --format env)"   # Terraform can't read `aws login` directly
terraform apply                                            # app is up ~2 minutes later
terraform destroy                                          # tear down when done
```

## How we used Kiro
- **Spec-driven development:** `.kiro/specs/bedrock-backend-integration/` contains the requirements Kiro wrote
  (EARS format) for wiring the Bedrock backend into the UI, including a negative-savings bug
  Kiro caught during requirements gathering.
- **Steering:** `.kiro/steering.md` and `.kiro/steering/architecture.md` give Kiro the architecture
  rules: Streamlit-only UI, Bedrock via boto3, forced tool calls for structured output, caching so
  reruns don't re-bill Bedrock.
- **Agent hooks:** `.kiro/hooks/` runs a JSON-contract check (`verify_contract.py`) whenever
  `app.py`/`backend.py` is saved and after every Kiro file edit, so an agent change can't silently
  rename a key the UI depends on.
- **What Kiro built:** the Terraform deployment and Dockerfile, the Energy Switch MA data script and
  per-utility rate documents, the property-based and live accuracy tests, and backend hardening
  (negative-savings clamp, divide-by-zero guard, constraint-aware chat answers).
- **Other tools:** the first backend prototype, the Streamlit app and the assisted BCCE sign-up were
  built with Claude Code.

## Data sources
Eversource MA rate summary (effective Sept 1, 2026), National Grid Basic Service rates (M.D.P.U. 1-26-I),
City of Boston BCCE program page and March 2026 notice, Energy Switch MA (MA DPU) supplier offers, and
Mass Save 2026 residential offers. Mass Save's site blocks automated downloads, so its rebates were
summarized from published offer pages. Check current amounts on masssave.com.

## Privacy & limitations
- Bills are processed in memory to produce results. They aren't written to disk or a database.
- The deployed demo runs over **HTTP, not HTTPS**. Use the sample bill rather than a real one.
- The sign-up helper bookmark contains your account details. Delete it after enrolling.
- BCCE is only for Eversource customers in the City of Boston. Rates are as of October 2026.
