"""
test_demo_data.py
-------------------
Tests for src/demo_data.py: generation of the 5 offline demo diagnoses,
valid case IDs, evidence grounding, correct ai_diagnoses.csv schema,
source_type = "Evaluation/Demo" labeling, no network/API calls, and
idempotent seeding.

Run from the project root:
    python3 -m pytest tests/test_demo_data.py -v
"""

import csv
import json
import os
import sys
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import demo_data  # noqa: E402
import diagnosis  # noqa: E402


@pytest.fixture
def out_csv(tmp_path):
    return str(tmp_path / "ai_diagnoses.csv")


# ---------------------------------------------------------------------------
# Generation of demo records
# ---------------------------------------------------------------------------

def test_get_demo_diagnoses_returns_exactly_five():
    records = demo_data.get_demo_diagnoses()
    assert len(records) == 5


def test_demo_diagnoses_cover_distinct_cases():
    records = demo_data.get_demo_diagnoses()
    case_ids = [r["case_id"] for r in records]
    assert len(set(case_ids)) == len(case_ids)


# ---------------------------------------------------------------------------
# Valid case IDs
# ---------------------------------------------------------------------------

def test_all_demo_case_ids_exist_in_cases_csv():
    for record in demo_data.get_demo_diagnoses():
        case = diagnosis.load_case(record["case_id"])  # raises if invalid
        assert case["case_id"] == record["case_id"]


def test_build_demo_result_invalid_case_id_raises(out_csv):
    bad = dict(demo_data.get_demo_diagnoses()[0])
    bad["case_id"] = "C999"
    with pytest.raises(diagnosis.CaseNotFoundError):
        demo_data.build_demo_result(bad)


# ---------------------------------------------------------------------------
# Evidence grounding
# ---------------------------------------------------------------------------

def test_every_demo_record_passes_evidence_grounding():
    """Reuses diagnosis.check_evidence_grounding directly -- the exact
    same function a real API response is checked against."""
    for raw in demo_data.get_demo_diagnoses():
        case = diagnosis.load_case(raw["case_id"])
        grounded, _, _ = diagnosis.check_evidence_grounding(
            raw["observed_evidence"], case["show_output"]
        )
        assert grounded, f"{raw['case_id']}'s observed_evidence is not grounded in its show_output"


def test_build_demo_result_rejects_fabricated_evidence(out_csv):
    fabricated = dict(demo_data.get_demo_diagnoses()[0])
    fabricated["observed_evidence"] = [
        "The router at 10.99.99.99 has a completely unrelated fabricated problem that never appears in this case"
    ]
    with pytest.raises(ValueError, match="isn't grounded"):
        demo_data.build_demo_result(fabricated)


# ---------------------------------------------------------------------------
# Correct schema (matches ai_diagnoses.csv / diagnose_case() shape)
# ---------------------------------------------------------------------------

def test_build_demo_result_matches_diagnose_case_schema():
    raw = demo_data.get_demo_diagnoses()[0]
    result = demo_data.build_demo_result(raw)
    expected_keys = {
        "case_id", "timestamp", "model", "root_cause", "confidence", "osi_layer",
        "observed_evidence", "inference", "next_command", "fix_steps",
        "human_review_required", "evidence_grounded", "ungrounded_evidence_items",
        "grounding_note", "checker_findings_count", "source_type",
    }
    assert expected_keys.issubset(result.keys())


def test_build_demo_result_confidence_is_valid_int():
    raw = demo_data.get_demo_diagnoses()[0]
    result = demo_data.build_demo_result(raw)
    assert isinstance(result["confidence"], int)
    assert 0 <= result["confidence"] <= 100


def test_build_demo_result_human_review_required_is_always_true():
    for raw in demo_data.get_demo_diagnoses():
        result = demo_data.build_demo_result(raw)
        assert result["human_review_required"] is True


# ---------------------------------------------------------------------------
# source_type = Evaluation/Demo
# ---------------------------------------------------------------------------

def test_build_demo_result_source_type_is_evaluation_demo():
    raw = demo_data.get_demo_diagnoses()[0]
    result = demo_data.build_demo_result(raw)
    assert result["source_type"] == "Evaluation/Demo"
    assert result["source_type"] == diagnosis.SOURCE_TYPE_EVALUATION_DEMO


def test_build_demo_result_never_labeled_real_ai_run():
    for raw in demo_data.get_demo_diagnoses():
        result = demo_data.build_demo_result(raw)
        assert result["source_type"] != diagnosis.SOURCE_TYPE_REAL_AI_RUN


def test_build_demo_result_model_label_indicates_no_api_call():
    raw = demo_data.get_demo_diagnoses()[0]
    result = demo_data.build_demo_result(raw)
    assert "no api call" in result["model"].lower()


# ---------------------------------------------------------------------------
# Persistence + reading back
# ---------------------------------------------------------------------------

def test_seed_demo_diagnoses_persists_all_five(out_csv):
    added = demo_data.seed_demo_diagnoses(out_csv=out_csv)
    assert len(added) == 5
    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 5
    assert all(r["source_type"] == "Evaluation/Demo" for r in rows)


def test_seeded_csv_matches_ai_diagnoses_fieldnames(out_csv):
    demo_data.seed_demo_diagnoses(out_csv=out_csv)
    with open(out_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
    assert header == diagnosis.CSV_FIELDNAMES


def test_seeded_observed_evidence_is_valid_json_list(out_csv):
    demo_data.seed_demo_diagnoses(out_csv=out_csv)
    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        parsed = json.loads(row["observed_evidence"])
        assert isinstance(parsed, list)
        assert len(parsed) > 0


def test_list_demo_diagnoses_returns_only_demo_rows(out_csv):
    demo_data.seed_demo_diagnoses(out_csv=out_csv)
    # Manually append a "Real AI Run" row to confirm filtering works.
    with open(out_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=diagnosis.CSV_FIELDNAMES)
        writer.writerow({
            "case_id": "C002", "timestamp": "2026-01-01T00:00:00+00:00",
            "model": "claude-sonnet-4-5", "root_cause": "real diagnosis",
            "confidence": "80", "osi_layer": "Layer 2",
            "observed_evidence": "[]", "inference": "x", "next_command": "",
            "fix_steps": "[]", "human_review_required": "True",
            "evidence_grounded": "True", "ungrounded_evidence_items": "[]",
            "grounding_note": "", "checker_findings_count": "0",
            "source_type": "Real AI Run",
        })
    demo_only = demo_data.list_demo_diagnoses(out_csv)
    assert len(demo_only) == 5
    assert all(r["source_type"] == "Evaluation/Demo" for r in demo_only)
    assert "C002" not in [r["case_id"] for r in demo_only]


def test_list_demo_diagnoses_empty_when_file_missing(tmp_path):
    missing = str(tmp_path / "does_not_exist.csv")
    assert demo_data.list_demo_diagnoses(missing) == []


# ---------------------------------------------------------------------------
# Idempotent seeding
# ---------------------------------------------------------------------------

def test_seed_demo_diagnoses_is_idempotent(out_csv):
    first = demo_data.seed_demo_diagnoses(out_csv=out_csv)
    second = demo_data.seed_demo_diagnoses(out_csv=out_csv)
    assert len(first) == 5
    assert len(second) == 0
    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 5  # no duplicates


def test_seed_demo_diagnoses_coexists_with_real_rows(out_csv):
    """Seeding demo data onto a file that already has a real AI run row
    must not disturb that row or double-count it as demo data."""
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=diagnosis.CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerow({
            "case_id": "C001", "timestamp": "2026-01-01T00:00:00+00:00",
            "model": "claude-sonnet-4-5", "root_cause": "a real diagnosis for C001",
            "confidence": "80", "osi_layer": "Layer 2",
            "observed_evidence": "[]", "inference": "x", "next_command": "",
            "fix_steps": "[]", "human_review_required": "True",
            "evidence_grounded": "True", "ungrounded_evidence_items": "[]",
            "grounding_note": "", "checker_findings_count": "0",
            "source_type": "Real AI Run",
        })
    added = demo_data.seed_demo_diagnoses(out_csv=out_csv)
    # C001 already has a demo-eligible slot occupied by a real row, but
    # since it wasn't already an Evaluation/Demo row, C001's demo version
    # still gets added alongside it (both are legitimate, distinctly
    # labeled rows for the same case).
    assert len(added) == 5
    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 6
    real_rows = [r for r in rows if r["source_type"] == "Real AI Run"]
    demo_rows = [r for r in rows if r["source_type"] == "Evaluation/Demo"]
    assert len(real_rows) == 1
    assert len(demo_rows) == 5


# ---------------------------------------------------------------------------
# No network/API calls
# ---------------------------------------------------------------------------

def test_seeding_never_imports_or_calls_anthropic_sdk(out_csv):
    """Patch diagnosis.call_model (the ONLY function in this project that
    ever talks to the Anthropic API) so that if demo_data.py somehow
    invoked it, this test would fail loudly."""
    with mock.patch.object(diagnosis, "call_model", side_effect=AssertionError(
        "demo_data.py must never call diagnosis.call_model (no API calls allowed)"
    )):
        added = demo_data.seed_demo_diagnoses(out_csv=out_csv)
    assert len(added) == 5  # completed successfully without ever touching call_model


def test_demo_data_module_has_no_network_or_device_automation_terms():
    forbidden_terms = ["anthropic.Anthropic", "requests.", "urllib", "socket.",
                        "paramiko", "netmiko", "telnetlib", "subprocess",
                        "os.system", "napalm", "ncclient"]
    src_path = os.path.join(os.path.dirname(__file__), "..", "src", "demo_data.py")
    with open(src_path, encoding="utf-8") as f:
        source = f.read()
    for term in forbidden_terms:
        assert term not in source, f"demo_data.py unexpectedly references {term!r}"


def test_no_api_key_required_to_seed_demo_data(out_csv, monkeypatch):
    """Seeding must succeed even with NO ANTHROPIC_API_KEY set at all --
    this is the whole point of the offline demo mode."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    added = demo_data.seed_demo_diagnoses(out_csv=out_csv)
    assert len(added) == 5
