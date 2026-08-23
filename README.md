# NetSage AI

An AI-assisted troubleshooter for Cisco Packet Tracer lab problems. It reads
symptoms and show-command output, suggests a likely root cause / OSI layer /
next command / fix, and **always requires a human to review** the diagnosis
before it's treated as accepted. Built for Project 2: Applied AI + Network
Troubleshooting (Cisco).

This repo currently covers **Phase 1 + Phase 2**: the case dataset, the AI
prompt library, the deterministic rule checker, and now the full AI
diagnosis layer (`src/diagnosis.py`) that calls the Anthropic API, validates
its JSON output, and checks that the AI's claimed evidence is actually
grounded in the case. Dashboard and human-review UI are scaffolded as empty
folders for Phase 3.

---

## Project Structure

```
netsage-ai/
├── cases/
│   ├── generate_cases.py       # Generates cases.csv (source of truth for the dataset)
│   └── cases.csv                # 38 troubleshooting cases (generated)
├── prompts/
│   ├── diagnose_prompt.md       # AI system prompt + 3 worked examples (strict JSON schema)
│   └── review_prompt.md         # Helper template for human reviewers
├── checker/
│   └── checker.py                # 6 deterministic rule checks (library + CLI)
├── src/
│   └── diagnosis.py              # Phase 2: AI diagnosis layer (Anthropic API + validation + CLI)
├── tests/
│   ├── test_checker.py           # 21 pytest tests for the rule checker (Phase 1)
│   └── test_diagnosis.py         # 40 pytest tests for the AI diagnosis layer (Phase 2)
├── ai_diagnoses.csv               # Created after your first successful AI diagnosis run
├── dashboard/                    # Reserved for Phase 3
├── responsible_ai_log/           # Reserved for Phase 3 (corrections_log.csv)
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## Setup

```bash
cd netsage-ai
python3 -m venv venv && source venv/bin/activate   # optional but recommended
pip install -r requirements.txt
cp .env.example .env      # only needed if you'll call the Anthropic API directly
```

## Regenerate the case dataset

`cases.csv` is generated, not hand-edited. To change or add cases, edit
`cases/generate_cases.py` and rerun it:

```bash
cd cases
python3 generate_cases.py
```

This writes `cases/cases.csv` and prints a per-category count summary.

## Run the deterministic rule checker

Against the whole case dataset:
```bash
cd checker
python3 checker.py --cases ../cases/cases.csv
```

Against a single hand-built network snapshot (JSON file with `hosts`,
`interfaces`, `vlans`, `switchports`, `routes`, `required_routes` keys —
see any `network_snapshot_json` value in `cases.csv` for the shape):
```bash
python3 checker.py --snapshot my_snapshot.json
```

Write full JSON results (useful for the Phase 2 dashboard):
```bash
python3 checker.py --cases ../cases/cases.csv --json-out ../checker_results.json
```

## Run the tests

From the project root:
```bash
python3 -m pytest tests/ -v
```

All 21 tests currently pass. See **Testing** below for what's covered.

## Run an AI diagnosis (Phase 2)

Requires a real `ANTHROPIC_API_KEY` — copy `.env.example` to `.env` and fill
it in, or export it in your shell. **The system will never fabricate a
diagnosis if the key is missing** — it prints a clear error and stops.

```bash
cd src
python3 diagnosis.py --case-id C007          # diagnose one case
python3 diagnosis.py --all                    # diagnose every case in cases.csv
python3 diagnosis.py --list                   # list valid case IDs (no key needed)
python3 diagnosis.py --case-id C007 --no-save # print only, don't write to CSV
python3 diagnosis.py --case-id C007 --model claude-opus-4-8   # override model
```

Each run prints the structured diagnosis (root cause, confidence 0-100,
OSI layer, observed evidence, inference, next command, fix steps) and
appends a row to `ai_diagnoses.csv` at the project root. It never edits
any device config — a human still has to review it (see
`prompts/review_prompt.md`).

See **The AI Diagnosis Layer** section below for how validation and
evidence grounding work.

---

## The AI Diagnosis Layer (Phase 2)

`src/diagnosis.py` implements the full pipeline for one case:

1. **Load** the case from `cases.csv` (raises a clear `CaseNotFoundError`
   for an invalid `--case-id`).
2. **Load** the system prompt from `prompts/diagnose_prompt.md`.
3. **Run** the Phase 1 deterministic checker against the case's
   `network_snapshot_json` and attach its findings to the prompt context
   — the AI is told these are a hint to verify, not ground truth.
4. **Call** the Anthropic API with the system prompt + the case's
   symptom/topology/evidence/checker-findings as the user message.
   Retries transient failures (rate limits, timeouts, connection errors)
   a couple of times with backoff; fails immediately (no retry) on bad
   auth.
5. **Parse** the response as JSON (stripping accidental Markdown fences),
   raising `MalformedJSONError` (with the raw text attached) if it isn't
   valid JSON or isn't a JSON object.
6. **Validate** the parsed object against the required schema — every
   missing field or wrong type is collected into one `ValidationError`
   message rather than failing on the first problem. `confidence` must
   be a number 0-100 (coerced to `int`). **`human_review_required` is
   always forced to `True`**, even if the model returns `false` — this
   is a hard product rule, not something the AI can waive.
7. **Check evidence grounding**: every `observed_evidence` string is
   checked against the case's actual `show_output` text using two
   signals — (a) any IP-address-looking token in the claimed evidence
   must appear verbatim in the real evidence, and (b) at least 40% of
   the claim's meaningful words must appear in the real evidence. A
   claim that fails either check is **flagged, not silently accepted**:
   it shows up in `ungrounded_evidence_items` and
   `evidence_grounded=False` in the output and the CSV.
8. **Save** the result to `ai_diagnoses.csv` (unless `--no-save`).

### Required JSON schema

```json
{
  "root_cause": "",
  "confidence": 0,
  "osi_layer": "",
  "observed_evidence": [],
  "inference": "",
  "next_command": "",
  "fix_steps": [],
  "human_review_required": true
}
```
`confidence` is an integer 0-100. See `prompts/diagnose_prompt.md` for the
full field-by-field instructions and three worked examples.

### Error handling

Every failure mode has its own exception class and a plain-English CLI
message (never a raw traceback for expected errors):

| Situation | Exception | CLI behavior |
|---|---|---|
| No `ANTHROPIC_API_KEY` set | `MissingAPIKeyError` | Clear message, exit 1, **nothing written to CSV, no fabricated diagnosis** |
| Invalid `--case-id` | `CaseNotFoundError` | Clear message naming the bad ID, exit 1 |
| `anthropic` package not installed | `APICallError` | Message pointing at `pip install -r requirements.txt` |
| Bad API key / auth failure | `APICallError` | No retry (retrying won't fix a bad key) |
| Rate limited / timeout / connection error | `APICallError` | Retried up to 2 times with backoff, then a clear error |
| AI response isn't valid JSON | `MalformedJSONError` | Message + first 500 chars of the raw response for debugging |
| AI response missing/wrong-typed fields | `ValidationError` | Lists every problem found in one message |

### ai_diagnoses.csv columns

`case_id, timestamp, model, root_cause, confidence, osi_layer,
observed_evidence, inference, next_command, fix_steps,
human_review_required, evidence_grounded, ungrounded_evidence_items,
grounding_note, checker_findings_count`

(`observed_evidence` and `fix_steps` are stored as JSON-encoded lists so
the CSV stays flat and importable into Excel/Sheets/pandas.)

---

## The Case Dataset

`cases.csv` has 38 cases across all fault categories from the problem
statement:

| Category  | Count |
|-----------|-------|
| VLAN      | 6     |
| Gateway   | 6     |
| DHCP      | 5     |
| DNS       | 4     |
| Routing   | 6     |
| ACL       | 4     |
| NAT       | 4     |
| Wireless  | 3     |
| **Total** | **38**|

Each row has these columns:

| Column | Description |
|---|---|
| `case_id` | e.g. `C001` |
| `category` | VLAN / Gateway / DHCP / DNS / Routing / ACL / NAT / Wireless |
| `concept_tag` | short machine-friendly fault tag |
| `severity` | Low / Medium / High |
| `osi_layer` | e.g. "Layer 2", "Layer 3/4" |
| `symptom` | what the student observes |
| `topology_note` | brief relevant topology |
| `show_output` | realistic Cisco show-command evidence (multi-command blob) |
| `expected_fault` | ground-truth explanation, for grading AI answers |
| `expected_next_command` | ground-truth next diagnostic step |
| `network_snapshot_json` | structured extract of the same evidence, consumed by `checker.py` |
| `checker_findable` | `True`/`False` — whether one of the 6 deterministic rules should catch this case |
| `checker_check` | which check(s) should fire, or why none apply |

Two cases (`C022`, `C030`) are the two worked examples given directly in the
Cisco problem statement, reproduced with matching evidence and OSI-layer
framing.

**Design note:** roughly half the cases (DNS nuance, ACL logic, NAT
misconfig, VLAN-exists-but-wrong-one, trunk/native-VLAN mismatch, OSPF
adjacency) are *intentionally* outside the scope of the 6 deterministic
checks. Those require the AI's reasoning over evidence, not a fixed rule —
this mirrors the real workflow where the rule checker catches the
"obvious/structural" bugs fast, and the AI (with human review) handles the
subtler ones.

---

## The Deterministic Rule Checker

`checker/checker.py` implements exactly the 6 required checks:

1. **`duplicate_ip`** — two hosts/interfaces sharing the same IP address.
2. **`wrong_subnet_mask`** — a host's mask doesn't match its VLAN's router
   interface mask.
3. **`gateway_mismatch`** — a host's default gateway doesn't match the
   VLAN's actual router interface IP (or, if that's unknown, isn't even in
   the host's own subnet).
4. **`interface_down`** — an interface isn't both `status=up` and
   `protocol=up`.
5. **`missing_vlan`** — a switchport/AP references a VLAN ID that isn't in
   the VLAN database.
6. **`missing_route`** — a required destination network is absent from the
   routing table.

It works off a small structured **network snapshot** (JSON) rather than
trying to regex-parse arbitrary Cisco CLI text — this keeps the checks
100% deterministic and testable, while `cases.csv` still carries the full,
realistic show-command text as the evidence the AI prompt reasons over.

Run it as a library:
```python
from checker import run_all_checks
findings = run_all_checks(snapshot_dict)
```
or as a CLI (see Setup above).

---

## Testing

Run everything with `python3 -m pytest tests/ -v` from the project root.
There are **61 tests total** across two files, and all 61 pass.

### `tests/test_checker.py` (21 tests, Phase 1)

- 2–3 unit tests per deterministic check (positive, negative/clean, and an
  edge case like "don't guess if we don't have a reference value").
- A combinator test verifying `run_all_checks` correctly aggregates
  findings from multiple simultaneous issues, and returns nothing for a
  clean snapshot.
- An integration test that runs the checker against the *entire*
  `cases.csv` and asserts its flagged/clean verdict matches the
  `checker_findable` ground-truth column baked into every case.
- A coverage test asserting all 6 checks fire at least once across the
  dataset (so a silently-broken rule can't hide).

### `tests/test_diagnosis.py` (40 tests, Phase 2)

- **JSON parsing** — valid JSON, fenced/unfenced Markdown code blocks,
  invalid JSON, non-object JSON (list or bare scalar).
- **Schema validation** — a fully valid diagnosis passes; each required
  field missing (individually and in combination) raises `ValidationError`
  naming the field; wrong types (`confidence` as a string, out of
  0-100 range, negative; `observed_evidence`/`fix_steps` not a list of
  strings) are all rejected; `human_review_required` is forced to `True`
  even when the model tries to set it `False`; float confidence is
  rounded to the nearest integer.
- **Evidence grounding** — real evidence passes; a fabricated IP address
  is flagged; an entirely unrelated claim is flagged; a paraphrased-but-
  real fact still passes; an empty evidence list is trivially grounded
  but noted; a mixed list correctly separates grounded from ungrounded
  items.
- **Missing API key** — `get_api_key()`, `call_model()`,
  `diagnose_case()`, and `diagnose_all_cases()` all raise
  `MissingAPIKeyError` immediately, and we explicitly assert **no file is
  written** to the output CSV when the key is missing (no fabricated
  diagnoses).
- **Invalid case ID** — `load_case()` and `diagnose_case()` raise
  `CaseNotFoundError` for a bad ID, and this check happens *before* any
  API key is even needed.
- **End-to-end pipeline** (model call mocked, no real network access) —
  a full valid run saves a correct row to CSV; a fabricated-evidence
  response is correctly flagged as ungrounded in both the return value
  and the CSV; malformed JSON and missing-field responses raise the
  right exceptions; a batch run continues past one failing case instead
  of aborting the whole batch.
- **Prompt loading** — the system prompt contains every required schema
  field name; a missing prompt file raises a clear error.

### Mutation testing (manual verification the tests have teeth)

We don't just trust that green tests mean correct code — we verified it:

1. Injected a bug into `check_interface_down` (Phase 1: `or`→`and` in the
   up/up condition) → the suite correctly failed
   `test_interface_partial_up_down_detected`. Reverted, suite green again.
2. Injected a bug into evidence grounding (Phase 2: hardcoded
   `grounded = True`, skipping the real check) → 3 grounding tests
   correctly failed. Reverted, suite green again.
3. Injected a bug into `get_api_key()` (Phase 2: disabled the missing-key
   check) → 5 API-key tests correctly failed, and — notably — the code
   then actually *attempted* a real Anthropic API call and got a genuine
   SDK-level authentication error, confirming the system doesn't silently
   fabricate a result even when our own safety check is broken. Reverted,
   suite green again.

All three mutations were caught, fixed, and the full 61-test suite passes
cleanly after each revert.

---

## Responsible AI / Human Review

Every AI diagnosis produced with `prompts/diagnose_prompt.md` is a
**recommendation only**. `prompts/review_prompt.md` defines the fields a
human reviewer fills in (`Accepted` / `Edited` / `Rejected` + notes). Any
`Edited` or `Rejected` case must be logged in
`responsible_ai_log/corrections_log.csv` (Phase 2) with an explanation of
why the AI got it wrong — the project requires at least 5 such logged
corrections.

---

## Phase Status

- [x] Phase 1: case dataset, prompt library, deterministic rule checker + tests
- [x] Phase 2: AI diagnosis layer (`src/diagnosis.py`), JSON validation, evidence
      grounding, `ai_diagnoses.csv` storage, CLI, tests
- [ ] Phase 3: dashboard, human review workflow, Responsible AI log, demo video

See the delivery notes in the assistant's chat response for what was built,
tested, and what still needs manual work (an API key, running
`diagnosis.py --all` against all 38 cases, and doing the actual human
review pass to populate the Responsible AI log).
