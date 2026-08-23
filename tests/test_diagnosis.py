"""
test_diagnosis.py
------------------
Phase 2 tests for src/diagnosis.py: JSON parsing, required-field
validation, malformed JSON, missing fields, evidence grounding, missing
API key, and invalid case ID handling.

These tests never make a real network call. Wherever a test needs the
"AI's raw response", it supplies a canned string directly to the parsing/
validation/grounding functions, or monkeypatches diagnosis.call_model.
This keeps the suite fast, free, and deterministic -- exactly what you
want for CI and for grading.

Run from the project root:
    python3 -m pytest tests/ -v
"""

import csv
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import diagnosis  # noqa: E402


CASES_CSV = os.path.join(os.path.dirname(__file__), "..", "cases", "cases.csv")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

VALID_DIAGNOSIS = {
    "root_cause": "PC2's default gateway does not match VLAN 10's router interface.",
    "confidence": 90,
    "osi_layer": "Layer 3",
    "observed_evidence": [
        "ipconfig on PC2 shows Default Gateway: 192.168.1.1",
        "show ip interface brief shows GigabitEthernet0/0.10 is 192.168.10.1",
    ],
    "inference": "The gateway is outside PC2's subnet, so off-subnet traffic never reaches it.",
    "next_command": "",
    "fix_steps": ["Set PC2's default gateway to 192.168.10.1", "Verify with ping"],
    "human_review_required": True,
}

SAMPLE_SHOW_OUTPUT = """
PC2> ipconfig
IP Address......: 192.168.10.55
Subnet Mask......: 255.255.255.0
Default Gateway...: 192.168.1.1

Router#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0.10  192.168.10.1    YES manual up                    up
"""


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every test starts with no ANTHROPIC_API_KEY unless it sets one
    explicitly -- avoids leaking a real key from the host environment into
    tests that are specifically checking the "missing key" path."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def test_parse_ai_response_valid_json():
    raw = json.dumps(VALID_DIAGNOSIS)
    data = diagnosis.parse_ai_response(raw)
    assert data["root_cause"] == VALID_DIAGNOSIS["root_cause"]
    assert data["confidence"] == 90


def test_parse_ai_response_strips_markdown_fences():
    raw = "```json\n" + json.dumps(VALID_DIAGNOSIS) + "\n```"
    data = diagnosis.parse_ai_response(raw)
    assert data["root_cause"] == VALID_DIAGNOSIS["root_cause"]


def test_parse_ai_response_strips_fences_without_json_tag():
    raw = "```\n" + json.dumps(VALID_DIAGNOSIS) + "\n```"
    data = diagnosis.parse_ai_response(raw)
    assert data["confidence"] == 90


def test_parse_ai_response_invalid_json_raises():
    with pytest.raises(diagnosis.MalformedJSONError):
        diagnosis.parse_ai_response("this is not json { at all")


def test_parse_ai_response_malformed_error_carries_raw_text():
    raw = "definitely not json"
    with pytest.raises(diagnosis.MalformedJSONError) as exc_info:
        diagnosis.parse_ai_response(raw)
    assert exc_info.value.raw_text == raw


def test_parse_ai_response_rejects_non_object_json():
    # Valid JSON, but a list instead of an object.
    with pytest.raises(diagnosis.MalformedJSONError):
        diagnosis.parse_ai_response('["not", "an", "object"]')


def test_parse_ai_response_rejects_bare_json_scalar():
    with pytest.raises(diagnosis.MalformedJSONError):
        diagnosis.parse_ai_response("42")


# ---------------------------------------------------------------------------
# Required fields / schema validation
# ---------------------------------------------------------------------------

def test_validate_schema_accepts_valid_diagnosis():
    normalized = diagnosis.validate_diagnosis_schema(dict(VALID_DIAGNOSIS))
    assert normalized["confidence"] == 90
    assert normalized["human_review_required"] is True


def test_validate_schema_missing_field_raises():
    broken = dict(VALID_DIAGNOSIS)
    del broken["root_cause"]
    with pytest.raises(diagnosis.ValidationError, match="root_cause"):
        diagnosis.validate_diagnosis_schema(broken)


def test_validate_schema_missing_multiple_fields_lists_all():
    broken = dict(VALID_DIAGNOSIS)
    del broken["root_cause"]
    del broken["inference"]
    with pytest.raises(diagnosis.ValidationError) as exc_info:
        diagnosis.validate_diagnosis_schema(broken)
    msg = str(exc_info.value)
    assert "root_cause" in msg and "inference" in msg


def test_validate_schema_wrong_type_confidence_raises():
    broken = dict(VALID_DIAGNOSIS)
    broken["confidence"] = "very confident"
    with pytest.raises(diagnosis.ValidationError, match="confidence"):
        diagnosis.validate_diagnosis_schema(broken)


def test_validate_schema_confidence_out_of_range_raises():
    broken = dict(VALID_DIAGNOSIS)
    broken["confidence"] = 150
    with pytest.raises(diagnosis.ValidationError, match="confidence"):
        diagnosis.validate_diagnosis_schema(broken)


def test_validate_schema_confidence_negative_raises():
    broken = dict(VALID_DIAGNOSIS)
    broken["confidence"] = -5
    with pytest.raises(diagnosis.ValidationError, match="confidence"):
        diagnosis.validate_diagnosis_schema(broken)


def test_validate_schema_observed_evidence_must_be_list_of_strings():
    broken = dict(VALID_DIAGNOSIS)
    broken["observed_evidence"] = "should be a list, not a string"
    with pytest.raises(diagnosis.ValidationError, match="observed_evidence"):
        diagnosis.validate_diagnosis_schema(broken)


def test_validate_schema_fix_steps_must_be_list_of_strings():
    broken = dict(VALID_DIAGNOSIS)
    broken["fix_steps"] = [1, 2, 3]
    with pytest.raises(diagnosis.ValidationError, match="fix_steps"):
        diagnosis.validate_diagnosis_schema(broken)


def test_validate_schema_forces_human_review_required_true_even_if_model_said_false():
    sneaky = dict(VALID_DIAGNOSIS)
    sneaky["human_review_required"] = False
    normalized = diagnosis.validate_diagnosis_schema(sneaky)
    assert normalized["human_review_required"] is True


def test_validate_schema_accepts_float_confidence_and_rounds():
    ok = dict(VALID_DIAGNOSIS)
    ok["confidence"] = 87.6
    normalized = diagnosis.validate_diagnosis_schema(ok)
    assert normalized["confidence"] == 88


# ---------------------------------------------------------------------------
# Evidence grounding
# ---------------------------------------------------------------------------

def test_evidence_grounding_all_grounded():
    grounded, results, note = diagnosis.check_evidence_grounding(
        VALID_DIAGNOSIS["observed_evidence"], SAMPLE_SHOW_OUTPUT
    )
    assert grounded is True
    assert all(r["grounded"] for r in results)


def test_evidence_grounding_flags_fabricated_ip():
    fabricated = ["The router at 10.99.99.99 is misconfigured"]
    grounded, results, note = diagnosis.check_evidence_grounding(fabricated, SAMPLE_SHOW_OUTPUT)
    assert grounded is False
    assert results[0]["missing_ip_tokens"] == ["10.99.99.99"]


def test_evidence_grounding_flags_unrelated_claim():
    unrelated = ["The DNS server at the branch office rejected the zone transfer request"]
    grounded, results, note = diagnosis.check_evidence_grounding(unrelated, SAMPLE_SHOW_OUTPUT)
    assert grounded is False


def test_evidence_grounding_accepts_paraphrased_but_real_facts():
    # Not a verbatim quote, but every significant word is present in the evidence.
    paraphrased = ["PC2 default gateway is configured as 192.168.1.1"]
    grounded, results, note = diagnosis.check_evidence_grounding(paraphrased, SAMPLE_SHOW_OUTPUT)
    assert grounded is True


def test_evidence_grounding_empty_list_is_trivially_grounded_but_noted():
    grounded, results, note = diagnosis.check_evidence_grounding([], SAMPLE_SHOW_OUTPUT)
    assert grounded is True
    assert results == []
    assert "No observed_evidence" in note


def test_evidence_grounding_mixed_grounded_and_ungrounded():
    mixed = [
        "ipconfig on PC2 shows Default Gateway: 192.168.1.1",  # grounded
        "The switch at 172.16.5.5 has spanning-tree loops",     # fabricated IP
    ]
    grounded, results, note = diagnosis.check_evidence_grounding(mixed, SAMPLE_SHOW_OUTPUT)
    assert grounded is False
    assert results[0]["grounded"] is True
    assert results[1]["grounded"] is False


# ---------------------------------------------------------------------------
# Missing API key
# ---------------------------------------------------------------------------

def test_get_api_key_raises_when_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(diagnosis.MissingAPIKeyError):
        diagnosis.get_api_key()


def test_get_api_key_returns_value_when_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake-key")
    assert diagnosis.get_api_key() == "sk-ant-test-fake-key"


def test_call_model_raises_missing_api_key_before_any_network_call(monkeypatch):
    """Make sure we fail fast on a missing key -- never attempt to import
    or call the anthropic SDK when there's no key at all."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(diagnosis.MissingAPIKeyError):
        diagnosis.call_model("system prompt", "user message")


def test_diagnose_case_raises_missing_api_key_without_calling_api(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(diagnosis.MissingAPIKeyError):
        diagnosis.diagnose_case("C007", save_csv=False)


def test_diagnose_all_cases_raises_missing_api_key_immediately(monkeypatch):
    """diagnose_all_cases should fail fast once, not try (and fail) 38 times."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(diagnosis.MissingAPIKeyError):
        diagnosis.diagnose_all_cases(save_csv=False)


def test_no_response_is_fabricated_when_key_missing(monkeypatch, tmp_path):
    """Explicitly confirm nothing gets written to the output CSV when the
    key is missing -- the system must not fabricate a diagnosis."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out_csv = tmp_path / "ai_diagnoses.csv"
    with pytest.raises(diagnosis.MissingAPIKeyError):
        diagnosis.diagnose_case("C007", save_csv=True, out_csv=str(out_csv))
    assert not out_csv.exists()


# ---------------------------------------------------------------------------
# Invalid case ID
# ---------------------------------------------------------------------------

def test_load_case_invalid_id_raises():
    with pytest.raises(diagnosis.CaseNotFoundError):
        diagnosis.load_case("C999")


def test_load_case_valid_id_returns_dict():
    case = diagnosis.load_case("C007")
    assert case["case_id"] == "C007"
    assert "symptom" in case


def test_diagnose_case_invalid_id_raises_before_api_call(monkeypatch):
    """An invalid case ID should fail with CaseNotFoundError even with no
    API key configured -- case lookup happens first."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(diagnosis.CaseNotFoundError):
        diagnosis.diagnose_case("NOT_A_REAL_CASE", save_csv=False)


def test_list_case_ids_returns_all_38():
    ids = diagnosis.list_case_ids()
    assert len(ids) == 38
    assert "C001" in ids and "C038" in ids


# ---------------------------------------------------------------------------
# End-to-end pipeline with a mocked model call (no real network access)
# ---------------------------------------------------------------------------

def test_diagnose_case_end_to_end_with_mocked_model(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake-key")

    def fake_call_model(system_prompt, user_message, model=None, **kwargs):
        return json.dumps(VALID_DIAGNOSIS)

    monkeypatch.setattr(diagnosis, "call_model", fake_call_model)

    out_csv = tmp_path / "ai_diagnoses.csv"
    result = diagnosis.diagnose_case("C007", save_csv=True, out_csv=str(out_csv))

    assert result["case_id"] == "C007"
    assert result["confidence"] == 90
    assert result["evidence_grounded"] is True
    assert result["human_review_required"] is True
    assert out_csv.exists()

    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["case_id"] == "C007"
    assert json.loads(rows[0]["observed_evidence"]) == VALID_DIAGNOSIS["observed_evidence"]


def test_diagnose_case_end_to_end_flags_ungrounded_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake-key")

    fabricated_diagnosis = dict(VALID_DIAGNOSIS)
    fabricated_diagnosis["observed_evidence"] = [
        "The router at 10.123.45.67 shows a duplex mismatch"  # not in C007's evidence
    ]

    def fake_call_model(system_prompt, user_message, model=None, **kwargs):
        return json.dumps(fabricated_diagnosis)

    monkeypatch.setattr(diagnosis, "call_model", fake_call_model)

    out_csv = tmp_path / "ai_diagnoses.csv"
    result = diagnosis.diagnose_case("C007", save_csv=True, out_csv=str(out_csv))

    assert result["evidence_grounded"] is False
    assert len(result["ungrounded_evidence_items"]) == 1

    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["evidence_grounded"] == "False"


def test_diagnose_case_end_to_end_malformed_json_raises(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake-key")

    def fake_call_model(system_prompt, user_message, model=None, **kwargs):
        return "the model rambled instead of returning JSON"

    monkeypatch.setattr(diagnosis, "call_model", fake_call_model)

    with pytest.raises(diagnosis.MalformedJSONError):
        diagnosis.diagnose_case("C007", save_csv=False)


def test_diagnose_case_end_to_end_missing_field_raises(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake-key")

    incomplete = dict(VALID_DIAGNOSIS)
    del incomplete["fix_steps"]

    def fake_call_model(system_prompt, user_message, model=None, **kwargs):
        return json.dumps(incomplete)

    monkeypatch.setattr(diagnosis, "call_model", fake_call_model)

    with pytest.raises(diagnosis.ValidationError, match="fix_steps"):
        diagnosis.diagnose_case("C007", save_csv=False)


def test_diagnose_all_cases_continues_after_one_case_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake-key")

    def flaky_call_model(system_prompt, user_message, model=None, **kwargs):
        # Fail deterministically for exactly one case, succeed for the rest.
        if "C003" in user_message or "PC" not in user_message:
            pass
        return json.dumps(VALID_DIAGNOSIS)

    # Simpler: make call_model raise for a specific case by checking case_id
    # via a wrapper around diagnose_case instead, since call_model doesn't
    # receive case_id directly.
    original_diagnose_case = diagnosis.diagnose_case

    def wrapped_diagnose_case(case_id, **kwargs):
        if case_id == "C003":
            raise diagnosis.APICallError("simulated transient failure")
        return original_diagnose_case(case_id, **kwargs)

    monkeypatch.setattr(diagnosis, "call_model", flaky_call_model)
    monkeypatch.setattr(diagnosis, "diagnose_case", wrapped_diagnose_case)

    out_csv = tmp_path / "ai_diagnoses.csv"
    summary = diagnosis.diagnose_all_cases(out_csv=str(out_csv), save_csv=True)

    assert "C003" in [f["case_id"] for f in summary.failed]
    assert len(summary.succeeded) == 37
    assert len(summary.failed) == 1


# ---------------------------------------------------------------------------
# Prompt loading sanity
# ---------------------------------------------------------------------------

def test_load_system_prompt_contains_schema_fields():
    prompt = diagnosis.load_system_prompt()
    for field in ["root_cause", "confidence", "osi_layer", "observed_evidence",
                  "inference", "next_command", "fix_steps", "human_review_required"]:
        assert field in prompt


def test_load_system_prompt_missing_file_raises(tmp_path):
    fake_path = str(tmp_path / "does_not_exist.md")
    with pytest.raises(diagnosis.DiagnosisError):
        diagnosis.load_system_prompt(fake_path)
