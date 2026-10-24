# Requirements Document

## Introduction

The Energy Bill Coach is a Streamlit app (`app.py`) that reads a Massachusetts electricity bill and shows ways to save money. This feature connects the app's two entry points, `analyze_bill(image_bytes)` and `ask(question)`, to a real AWS Bedrock backend (`backend.py` from https://github.com/NithishBhat/hackachusetts-energy-coach). The backend uses Claude Sonnet 4.5 on Bedrock in `us-east-1` and Knowledge Base `ZK6VSBXXCK`. The adapters translate backend output into the app's frozen data contract so the UI does not change. The feature also adds dependency declarations and property-based tests for the savings math.

### Workspace notes (observed during requirements gathering)

- The local `backend.py` is byte-for-byte identical in content to `backend.py` on the `main` branch of the public repo. `sample_bills/sample_eversource_bill.png` is already present. The local repo's `origin` is that public repo.
- The local `app.py` does not contain placeholder `analyze_bill()` / `ask()` functions. The local `app.py` already imports `read_bill`, `find_savings` and `ask` from `backend.py`, caches a local `analyze()` helper with `st.cache_data`, and renders fields from the backend's native shape (`supply_rate_per_kwh`, `why`, `yearly_cost_now`). The local `app.py` does not reference `rate_per_kwh`, `monthly_kwh`, `valid_until`, `warning`, `name`, `service_address`, `meter_number` or `service_ref`.
- The local `requirements.txt` already lists `boto3`, `botocore[crt]` and `streamlit` (unpinned).
- The current Savings_Finder passes a Rebate_Idea's `estimated_yearly_savings` through unchanged (`or 0` only replaces null), so a negative model estimate would produce negative savings. Requirement 8.5 requires clamping to 0.
- These requirements assume the target `app.py` is the placeholder version with the frozen data contract described in the user request. The exact contract field list must be confirmed against that version of `app.py` before design.

## Glossary

- **App**: The Streamlit application defined in `app.py`, including the UI rendering code.
- **UI_Code**: All code in `app.py` that renders widgets, layout, text, metrics, charts, chat messages and buttons.
- **Backend**: The module `backend.py`, providing `read_bill(image_bytes)`, `find_savings(bill)` and `ask(question, bill, savings)`.
- **Bill_Reader**: The Backend function `read_bill(image_bytes)`, which sends a bill image or PDF to Bedrock and returns a Bill_Record.
- **Savings_Finder**: The Backend function `find_savings(bill)`, which retrieves Knowledge Base documents, asks the model for ideas, and returns a Savings_Result.
- **Backend_Ask**: The Backend function `ask(question, bill, savings)`, which returns `{"answer": str, "sources": [str]}`.
- **Yearly_Kwh_Estimator**: The Backend function `_yearly_kwh(bill)`, which estimates annual kWh usage for a Bill_Record.
- **Analyze_Adapter**: The function `analyze_bill(image_bytes)` in `app.py` that calls Bill_Reader and Savings_Finder and returns a Contract_Analysis.
- **Ask_Adapter**: The function `ask(question)` in `app.py` that calls Backend_Ask and returns a Contract_Answer.
- **Bill_Record**: The dict returned by Bill_Reader, with keys defined by `BILL_SCHEMA` in the Backend.
- **Bill_Schema**: The JSON schema `BILL_SCHEMA` in the Backend that the model fills through the `save_bill` tool.
- **Savings_Result**: The dict returned by Savings_Finder, with keys `yearly_cost_now`, `total_yearly_savings` and `recommendations`.
- **Supply_Switch**: A model-proposed idea of kind `supply_switch` that names a new supply rate in $/kWh.
- **Rebate_Idea**: A model-proposed idea of kind `rebate` that carries an estimated yearly savings in dollars.
- **Contract_Analysis**: The frozen data structure the UI_Code expects from `analyze_bill`, containing bill fields (including `name`, `service_address`, `meter_number`, `service_ref`, `rate_per_kwh`, `monthly_kwh`) and a `recommendations` list.
- **Contract_Recommendation**: One entry in the Contract_Analysis `recommendations` list, with keys `title`, `yearly_savings`, `link`, `valid_until` and `warning`.
- **Contract_Answer**: The dict `{"answer": str, "sources": [str]}` the UI_Code expects from `ask`.
- **Session_State**: Streamlit's `st.session_state` for the current browser session.
- **Analysis_Cache**: The Streamlit `st.cache_data` cache that stores Contract_Analysis results keyed by uploaded file bytes.
- **Test_Suite**: The automated pytest test files added by this feature, including Hypothesis property-based tests.
- **Requirements_File**: The file `requirements.txt` in the workspace root.

## Requirements

### Requirement 1: Backend source and sample bill

**User Story:** As a developer, I want the workspace to use `backend.py` and `sample_bills/` from the public repo, so that the App runs on the team's shared Bedrock backend.

#### Acceptance Criteria

1. THE Backend SHALL match the content of `backend.py` on the `main` branch of https://github.com/NithishBhat/hackachusetts-energy-coach, except for the Bill_Schema extensions defined in Requirement 2 and the non-negative savings fix required by Requirement 8.
2. THE workspace SHALL contain `sample_bills/sample_eversource_bill.png` with the same content as the file of the same path in the public repo.
3. THE Backend SHALL use model ID `us.anthropic.claude-sonnet-4-5-20250929-v1:0` in region `us-east-1` when the `BILL_MODEL_ID` environment variable is unset.
4. THE Backend SHALL use Knowledge Base ID `ZK6VSBXXCK` for all Knowledge Base retrievals.

### Requirement 2: Extract customer and meter identifiers from the bill

**User Story:** As a household user, I want the App to read my name, service address, meter number and service reference from my bill, so that the results screen shows the account the analysis belongs to.

#### Acceptance Criteria

1. THE Bill_Schema SHALL define the properties `name`, `service_address`, `meter_number` and `service_ref`, each with type string or null.
2. THE Bill_Schema SHALL list `name`, `service_address`, `meter_number` and `service_ref` as required keys.
3. WHEN a bill shows a value for `name`, `service_address`, `meter_number` or `service_ref`, THE Bill_Reader SHALL return that value as printed on the bill.
4. IF a bill does not show a value for `name`, `service_address`, `meter_number` or `service_ref`, THEN THE Bill_Reader SHALL return null for that key.
5. THE Bill_Reader SHALL return a Bill_Record that contains every required key of the Bill_Schema.

### Requirement 3: Analyze_Adapter maps backend results to the frozen contract

**User Story:** As a frontend developer, I want `analyze_bill(image_bytes)` to return the existing frozen data contract, so that the UI renders real Bedrock results without any UI change.

#### Acceptance Criteria

1. WHEN the Analyze_Adapter receives bill file bytes, THE Analyze_Adapter SHALL call the Bill_Reader with those bytes and then call the Savings_Finder with the returned Bill_Record.
2. THE Analyze_Adapter SHALL return a Contract_Analysis containing every key the UI_Code reads from the analysis result.
3. THE Analyze_Adapter SHALL set the Contract_Analysis `rate_per_kwh` to the Bill_Record `supply_rate_per_kwh` value.
4. THE Analyze_Adapter SHALL set the Contract_Analysis `monthly_kwh` to the Yearly_Kwh_Estimator result divided by 12.
5. THE Analyze_Adapter SHALL copy `name`, `service_address`, `meter_number` and `service_ref` from the Bill_Record into the Contract_Analysis.
6. THE Analyze_Adapter SHALL produce exactly one Contract_Recommendation for each entry in the Savings_Result `recommendations` list, in the same order.
7. THE Analyze_Adapter SHALL give every Contract_Recommendation the keys `title`, `yearly_savings`, `link`, `valid_until` and `warning`.
8. THE Analyze_Adapter SHALL copy `title`, `yearly_savings` and `link` from the corresponding Savings_Result recommendation into each Contract_Recommendation.
9. IF the Backend provides no value for a Contract_Recommendation `valid_until` or `warning`, THEN THE Analyze_Adapter SHALL set that key to null.
10. IF the Bill_Record `supply_rate_per_kwh` is null, THEN THE Analyze_Adapter SHALL set the Contract_Analysis `rate_per_kwh` to null.
11. IF the uploaded bytes are not a PNG, JPEG, WEBP or PDF file, THEN THE Analyze_Adapter SHALL raise the `ValueError` produced by the Bill_Reader.

### Requirement 4: Ask_Adapter uses the current bill and savings

**User Story:** As a household user, I want the chat to answer using my analyzed bill, so that answers quote the same savings figures the page shows.

#### Acceptance Criteria

1. WHEN the Ask_Adapter receives a question, THE Ask_Adapter SHALL call the Backend_Ask with the question, the Bill_Record stored in Session_State and the Savings_Result stored in Session_State.
2. WHILE Session_State holds no analyzed bill, THE Ask_Adapter SHALL call the Backend_Ask with the question and null values for bill and savings.
3. THE Ask_Adapter SHALL return a Contract_Answer containing only the keys `answer` and `sources`.
4. THE Ask_Adapter SHALL return the `answer` string and `sources` list exactly as returned by the Backend_Ask.
5. WHEN the Analyze_Adapter completes for a new bill, THE App SHALL store the Bill_Record and Savings_Result in Session_State for use by the Ask_Adapter.

### Requirement 5: Cache bill analysis

**User Story:** As a developer, I want analysis results cached, so that Streamlit reruns do not re-call Bedrock or incur repeat cost for the same bill.

#### Acceptance Criteria

1. THE App SHALL store Contract_Analysis results in the Analysis_Cache keyed by the uploaded file bytes.
2. WHEN the App reruns with file bytes already present in the Analysis_Cache, THE App SHALL return the cached Contract_Analysis without calling the Bill_Reader or the Savings_Finder.
3. WHEN the App receives file bytes not present in the Analysis_Cache, THE App SHALL call the Bill_Reader and the Savings_Finder exactly once for those bytes.

### Requirement 6: UI unchanged

**User Story:** As a frontend developer, I want the UI code left untouched, so that the integration does not break the agreed layout and copy.

#### Acceptance Criteria

1. THE App SHALL keep the UI_Code identical to the UI_Code in the pre-integration `app.py`.
2. THE App SHALL limit changes in `app.py` to the bodies of `analyze_bill` and `ask`, the imports they require, and the `st.cache_data` decorator on the analysis function.

### Requirement 7: Dependencies

**User Story:** As a developer, I want the AWS SDK dependencies declared, so that a fresh environment can run the App against Bedrock.

#### Acceptance Criteria

1. THE Requirements_File SHALL list `boto3`.
2. THE Requirements_File SHALL list `botocore[crt]`.
3. THE Requirements_File SHALL list `hypothesis` and `pytest` for running the Test_Suite.
4. THE Requirements_File SHALL keep every dependency listed before this feature.

### Requirement 8: Savings math correctness

**User Story:** As a household user, I want savings figures to be trustworthy, so that I am never shown a negative saving or a double-counted supply plan.

#### Acceptance Criteria

1. FOR ALL Bill_Records and model-proposed ideas, THE Savings_Finder SHALL return every recommendation with `yearly_savings` greater than or equal to 0.
2. FOR ALL Bill_Records and model-proposed ideas, THE Savings_Finder SHALL return `total_yearly_savings` greater than or equal to 0.
3. FOR ALL sets of Supply_Switch ideas with a new rate lower than the current supply rate, THE Savings_Finder SHALL include exactly one Supply_Switch recommendation, and that recommendation SHALL have the lowest new supply rate among those ideas.
4. IF a Supply_Switch idea has a null new rate, or the Bill_Record has a null current supply rate, or the new rate is greater than or equal to the current supply rate, THEN THE Savings_Finder SHALL exclude that Supply_Switch idea from the recommendations.
5. IF a Rebate_Idea has a negative `estimated_yearly_savings`, THEN THE Savings_Finder SHALL report a `yearly_savings` of 0 for that recommendation.
6. FOR ALL Savings_Results, THE Savings_Finder SHALL set `total_yearly_savings` equal to the sum of the recommendation `yearly_savings` values, rounded to 2 decimal places.
7. FOR ALL Bill_Records whose `monthly_kwh_history` holds 12 or more entries, THE Yearly_Kwh_Estimator SHALL return the sum of the kWh values of the last 12 entries.
8. FOR ALL Bill_Records whose `monthly_kwh_history` holds fewer than 12 entries, THE Yearly_Kwh_Estimator SHALL return the Bill_Record `kwh_used` multiplied by 12, treating a null `kwh_used` as 0.

### Requirement 9: Property-based test suite

**User Story:** As a developer, I want Hypothesis property tests for the savings math, so that regressions in the arithmetic are caught across many generated bills.

#### Acceptance Criteria

1. THE Test_Suite SHALL include Hypothesis property tests covering Requirement 8 criteria 1, 3 and 7.
2. THE Test_Suite SHALL replace Knowledge Base retrieval and Bedrock model calls with in-process test doubles, so that the Test_Suite runs without AWS credentials or network access.
3. WHEN a developer runs `pytest` from the workspace root, THE Test_Suite SHALL execute every property test with at least 100 generated examples per property.
