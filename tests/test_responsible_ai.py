"""
test_responsible_ai.py
-----------------------
Phase 3C tests for src/responsible_ai.py: valid record creation, invalid
case ID, invalid source_type/human_decision, missing required fields,
unsupported (ungrounded) evidence, persistence, loading existing records,
and validation of all 5 seeded Evaluation/Demo examples.

Run from the project root:
    python3 -m pytest tests/test_responsible_ai.py -v
"""

import csv
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import responsible_ai as rai  # noqa: E402
import diagnosis  # noqa: E402


VALID_KWARGS = dict(
    case_id="C002",
    source_type="Evaluation/Demo",
    ai_root_cause="VLAN 99 is missing from the VLAN database on SW2.",
    human_decision="Rejected",
    human_corrected_root_cause="Both trunk ends have a native VLAN mismatch (1 vs 99).",
    correction_reason="The AI pattern-matched to a missing-VLAN failure that doesn't fit this evidence.",
    supporting_evidence="Native VLAN mismatch discovered on Gi0/1 (1), with SW2 Gi0/1 (99)",
)


@pytest.fixture
def out_csv(tmp_path):
    return str(tmp_path / "responsible_ai_log.csv")


# ---------------------------------------------------------------------------
# 1. Valid record
# ---------------------------------------------------------------------------

def test_valid_record_saves_correctly(out_csv):
    result = rai.add_record(out_csv=out_csv, **VALID_KWARGS)
    assert result["case_id"] == "C002"
    assert result["source_type"] == "Evaluation/Demo"
    assert result["human_decision"] == "Rejected"
    assert os.path.exists(out_csv)


def test_valid_record_includes_timestamp(out_csv):
    result = rai.add_record(out_csv=out_csv, **VALID_KWARGS)
    assert result["timestamp"]  # non-empty


# ---------------------------------------------------------------------------
# 2. Invalid case ID
# ---------------------------------------------------------------------------

def test_invalid_case_id_raises(out_csv):
    bad = dict(VALID_KWARGS)
    bad["case_id"] = "C999"
    with pytest.raises(diagnosis.CaseNotFoundError):
        rai.add_record(out_csv=out_csv, **bad)


# ---------------------------------------------------------------------------
# 3. Invalid source type
# ---------------------------------------------------------------------------

def test_invalid_source_type_raises(out_csv):
    bad = dict(VALID_KWARGS)
    bad["source_type"] = "Guessed"
    with pytest.raises(rai.InvalidSourceTypeError):
        rai.add_record(out_csv=out_csv, **bad)


def test_source_type_case_sensitive(out_csv):
    bad = dict(VALID_KWARGS)
    bad["source_type"] = "evaluation/demo"  # wrong case
    with pytest.raises(rai.InvalidSourceTypeError):
        rai.add_record(out_csv=out_csv, **bad)


# ---------------------------------------------------------------------------
# 4. Invalid human decision
# ---------------------------------------------------------------------------

def test_invalid_human_decision_raises(out_csv):
    bad = dict(VALID_KWARGS)
    bad["human_decision"] = "Accepted"  # not a valid Responsible AI decision
    with pytest.raises(rai.InvalidHumanDecisionError):
        rai.add_record(out_csv=out_csv, **bad)


def test_human_decision_only_edited_or_rejected(out_csv):
    for good_value in ("Edited", "Rejected"):
        ok = dict(VALID_KWARGS)
        ok["human_decision"] = good_value
        result = rai.add_record(out_csv=out_csv, **ok)
        assert result["human_decision"] == good_value


# ---------------------------------------------------------------------------
# 5. Missing AI diagnosis
# ---------------------------------------------------------------------------

def test_missing_ai_root_cause_raises(out_csv):
    bad = dict(VALID_KWARGS)
    bad["ai_root_cause"] = ""
    with pytest.raises(rai.MissingAIRootCauseError):
        rai.add_record(out_csv=out_csv, **bad)


def test_whitespace_only_ai_root_cause_raises(out_csv):
    bad = dict(VALID_KWARGS)
    bad["ai_root_cause"] = "   "
    with pytest.raises(rai.MissingAIRootCauseError):
        rai.add_record(out_csv=out_csv, **bad)


# ---------------------------------------------------------------------------
# 6. Missing correction
# ---------------------------------------------------------------------------

def test_missing_human_corrected_root_cause_raises(out_csv):
    bad = dict(VALID_KWARGS)
    bad["human_corrected_root_cause"] = ""
    with pytest.raises(rai.MissingCorrectionError):
        rai.add_record(out_csv=out_csv, **bad)


# ---------------------------------------------------------------------------
# 7. Missing correction reason
# ---------------------------------------------------------------------------

def test_missing_correction_reason_raises(out_csv):
    bad = dict(VALID_KWARGS)
    bad["correction_reason"] = ""
    with pytest.raises(rai.MissingCorrectionReasonError):
        rai.add_record(out_csv=out_csv, **bad)


# ---------------------------------------------------------------------------
# 8. Missing supporting evidence
# ---------------------------------------------------------------------------

def test_missing_supporting_evidence_raises(out_csv):
    bad = dict(VALID_KWARGS)
    bad["supporting_evidence"] = ""
    with pytest.raises(rai.MissingSupportingEvidenceError):
        rai.add_record(out_csv=out_csv, **bad)


# ---------------------------------------------------------------------------
# 9. Unsupported (ungrounded) evidence
# ---------------------------------------------------------------------------

def test_evidence_not_supported_by_case_raises(out_csv):
    bad = dict(VALID_KWARGS)
    bad["supporting_evidence"] = "The router at 10.99.99.99 has a completely unrelated fabricated problem"
    with pytest.raises(rai.EvidenceNotGroundedError):
        rai.add_record(out_csv=out_csv, **bad)


def test_evidence_from_a_different_case_is_rejected(out_csv):
    """Evidence that's real, but from a DIFFERENT case than the one
    cited, should still be rejected as ungrounded for this case_id."""
    bad = dict(VALID_KWARGS)
    bad["case_id"] = "C018"  # DNS case
    bad["supporting_evidence"] = "Native VLAN mismatch discovered on Gi0/1 (1), with SW2 Gi0/1 (99)"  # from C002
    with pytest.raises(rai.EvidenceNotGroundedError):
        rai.add_record(out_csv=out_csv, **bad)


# ---------------------------------------------------------------------------
# 10. Successful persistence
# ---------------------------------------------------------------------------

def test_record_persists_all_required_fields_to_csv(out_csv):
    rai.add_record(out_csv=out_csv, **VALID_KWARGS)
    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    for field in rai.REQUIRED_RECORD_FIELDS:
        assert field in rows[0]
    assert rows[0]["case_id"] == "C002"
    assert rows[0]["source_type"] == "Evaluation/Demo"


def test_multiple_records_append_rather_than_overwrite(out_csv):
    rai.add_record(out_csv=out_csv, **VALID_KWARGS)
    second = dict(VALID_KWARGS)
    second["case_id"] = "C018"
    second["ai_root_cause"] = "DNS server is down."
    second["human_corrected_root_cause"] = "PC8 has the wrong DNS server configured."
    second["correction_reason"] = "The AI missed the direct evidence in ipconfig."
    second["supporting_evidence"] = "DNS Servers......: 203.0.113.9"
    rai.add_record(out_csv=out_csv, **second)
    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# 11. Loading existing records
# ---------------------------------------------------------------------------

def test_list_records_returns_saved_record(out_csv):
    rai.add_record(out_csv=out_csv, **VALID_KWARGS)
    rows = rai.list_records(csv_path=out_csv)
    assert len(rows) == 1
    assert rows[0]["case_id"] == "C002"


def test_list_records_filters_by_case_id(out_csv):
    rai.add_record(out_csv=out_csv, **VALID_KWARGS)
    second = dict(VALID_KWARGS)
    second["case_id"] = "C018"
    second["ai_root_cause"] = "DNS server is down."
    second["human_corrected_root_cause"] = "PC8 has the wrong DNS server configured."
    second["correction_reason"] = "The AI missed the direct evidence in ipconfig."
    second["supporting_evidence"] = "DNS Servers......: 203.0.113.9"
    rai.add_record(out_csv=out_csv, **second)

    all_rows = rai.list_records(csv_path=out_csv)
    c002_rows = rai.list_records("C002", csv_path=out_csv)
    assert len(all_rows) == 2
    assert len(c002_rows) == 1
    assert c002_rows[0]["case_id"] == "C002"


def test_list_records_returns_empty_list_when_no_log_exists(tmp_path):
    nonexistent = str(tmp_path / "does_not_exist.csv")
    assert rai.list_records(csv_path=nonexistent) == []


# ---------------------------------------------------------------------------
# 12. Validation of all 5 evaluation examples
# ---------------------------------------------------------------------------

def test_get_demo_examples_returns_at_least_five():
    examples = rai.get_demo_examples()
    assert len(examples) >= 5


def test_all_demo_examples_are_evaluation_demo_source_type():
    for example in rai.get_demo_examples():
        assert example["source_type"] == "Evaluation/Demo"


def test_all_demo_examples_reference_real_case_ids():
    for example in rai.get_demo_examples():
        case = diagnosis.load_case(example["case_id"])  # raises if invalid
        assert case["case_id"] == example["case_id"]


def test_all_demo_examples_use_different_human_decisions_where_applicable():
    decisions = {e["human_decision"] for e in rai.get_demo_examples()}
    assert decisions.issubset(set(rai.VALID_HUMAN_DECISIONS))
    # The assignment asks for varied failure modes; confirm we're not
    # using only one decision type across all 5.
    assert len(decisions) >= 2


def test_each_demo_example_passes_full_validation(out_csv):
    """This is the core requirement: every one of the 5 examples must
    pass validate_record() cleanly, including the evidence-grounding
    check against that case's real show_output."""
    for example in rai.get_demo_examples():
        normalized = rai.validate_record(dict(example, timestamp="2026-01-01T00:00:00+00:00"))
        assert normalized["case_id"] == example["case_id"]


def test_seed_demo_examples_persists_all_five(out_csv):
    added = rai.seed_demo_examples(out_csv=out_csv)
    assert len(added) == 5
    rows = rai.list_records(csv_path=out_csv)
    assert len(rows) == 5
    assert all(r["source_type"] == "Evaluation/Demo" for r in rows)


def test_seed_demo_examples_is_idempotent(out_csv):
    first = rai.seed_demo_examples(out_csv=out_csv)
    second = rai.seed_demo_examples(out_csv=out_csv)
    assert len(first) == 5
    assert len(second) == 0  # nothing new added on the second call
    rows = rai.list_records(csv_path=out_csv)
    assert len(rows) == 5  # no duplicates


def test_demo_examples_cover_distinct_case_ids():
    """Sanity check that the 5 examples aren't all the same case
    repeated -- the assignment wants varied failure patterns."""
    case_ids = [e["case_id"] for e in rai.get_demo_examples()]
    assert len(set(case_ids)) == len(case_ids)


# ---------------------------------------------------------------------------
# Safety: module never touches network/device configuration
# ---------------------------------------------------------------------------

def test_module_has_no_device_automation_capability():
    forbidden_terms = ["paramiko", "netmiko", "telnetlib", "subprocess",
                        "os.system", "napalm", "ncclient"]
    src_path = os.path.join(os.path.dirname(__file__), "..", "src", "responsible_ai.py")
    with open(src_path, encoding="utf-8") as f:
        source = f.read()
    for term in forbidden_terms:
        assert term not in source, f"responsible_ai.py unexpectedly references {term!r}"
