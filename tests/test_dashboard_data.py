"""
test_dashboard_data.py
------------------------
Phase 4 tests for app/dashboard_data.py: CSV loading (including graceful
handling of missing files), case bundling for the Case Explorer, and
overview statistics computation (including malformed-row tolerance).

These are pure-Python tests -- no Streamlit session needed, since
dashboard_data.py has no Streamlit dependency.

Run from the project root:
    python3 -m pytest tests/test_dashboard_data.py -v
"""

import csv
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_data as dd  # noqa: E402
import diagnosis  # noqa: E402
import review  # noqa: E402
import verify  # noqa: E402
import responsible_ai as rai  # noqa: E402


# ---------------------------------------------------------------------------
# Loading: real data
# ---------------------------------------------------------------------------

def test_load_cases_returns_all_38():
    cases = dd.load_cases()
    assert len(cases) == 38
    assert all("case_id" in c for c in cases)


# ---------------------------------------------------------------------------
# Loading: missing files -> empty list, never an error, never fabricated data
# ---------------------------------------------------------------------------

def test_load_cases_missing_file_returns_empty_list(tmp_path):
    missing = str(tmp_path / "does_not_exist.csv")
    assert dd.load_cases(missing) == []


def test_load_ai_diagnoses_missing_file_returns_empty_list(tmp_path):
    missing = str(tmp_path / "does_not_exist.csv")
    assert dd.load_ai_diagnoses(missing) == []


def test_load_reviews_missing_file_returns_empty_list(tmp_path):
    missing = str(tmp_path / "does_not_exist.csv")
    assert dd.load_reviews(missing) == []


def test_load_verifications_missing_file_returns_empty_list(tmp_path):
    missing = str(tmp_path / "does_not_exist.csv")
    assert dd.load_verifications(missing) == []


def test_load_responsible_ai_missing_file_returns_empty_list(tmp_path):
    missing = str(tmp_path / "does_not_exist.csv")
    assert dd.load_responsible_ai(missing) == []


# ---------------------------------------------------------------------------
# Case bundle
# ---------------------------------------------------------------------------

def test_get_case_bundle_invalid_case_returns_none():
    assert dd.get_case_bundle("C999") is None


def test_get_case_bundle_valid_case_with_no_other_data(tmp_path):
    empty = str(tmp_path / "empty.csv")
    bundle = dd.get_case_bundle(
        "C001", ai_csv=empty, review_csv=empty, verification_csv=empty,
    )
    assert bundle is not None
    assert bundle["case"]["case_id"] == "C001"
    assert bundle["ai_diagnosis"] is None
    assert bundle["review"] is None
    assert bundle["verification"] is None


def test_get_case_bundle_picks_latest_review_by_timestamp(tmp_path):
    review_csv = tmp_path / "review_log.csv"
    with open(review_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=review.CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerow({
            "case_id": "C001", "ai_root_cause": "x", "ai_confidence": "50",
            "review_status": "Accepted", "final_root_cause": "first pass",
            "reviewer_notes": "", "evidence_used": "[]",
            "timestamp": "2026-01-01T00:00:00+00:00",
        })
        writer.writerow({
            "case_id": "C001", "ai_root_cause": "x", "ai_confidence": "50",
            "review_status": "Edited", "final_root_cause": "second pass, corrected",
            "reviewer_notes": "refined", "evidence_used": "[]",
            "timestamp": "2026-01-02T00:00:00+00:00",
        })
    empty = str(tmp_path / "empty.csv")
    bundle = dd.get_case_bundle(
        "C001", ai_csv=empty, review_csv=str(review_csv), verification_csv=empty,
    )
    assert bundle["review"]["final_root_cause"] == "second pass, corrected"


# ---------------------------------------------------------------------------
# Overview stats: no data yet
# ---------------------------------------------------------------------------

def test_overview_stats_with_no_ai_review_or_verification_data(tmp_path):
    empty = str(tmp_path / "empty.csv")
    stats = dd.compute_overview_stats(
        ai_csv=empty, review_csv=empty, verification_csv=empty,
    )
    assert stats["total_cases"] == 38
    assert stats["ai_diagnoses_count"] == 0
    assert stats["accepted_count"] == 0
    assert stats["edited_count"] == 0
    assert stats["rejected_count"] == 0
    assert stats["resolved_count"] == 0
    assert stats["not_resolved_count"] == 0
    assert stats["inconclusive_count"] == 0
    # Category/severity counts come from cases.csv, which always exists.
    assert sum(stats["category_counts"].values()) == 38
    assert sum(stats["severity_counts"].values()) == 38
    assert "VLAN" in stats["category_counts"]


# ---------------------------------------------------------------------------
# Overview stats: with real review/verification data
# ---------------------------------------------------------------------------

def _write_review_rows(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=review.CSV_FIELDNAMES)
        writer.writeheader()
        for status, case_id in rows:
            writer.writerow({
                "case_id": case_id, "ai_root_cause": "x", "ai_confidence": "50",
                "review_status": status, "final_root_cause": "y",
                "reviewer_notes": "", "evidence_used": "[]",
                "timestamp": "2026-01-01T00:00:00+00:00",
            })


def _write_verification_rows(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=verify.CSV_FIELDNAMES)
        writer.writeheader()
        for status, case_id in rows:
            writer.writerow({
                "case_id": case_id, "final_diagnosis": "y", "verification_status": status,
                "before_evidence": "a", "after_evidence": "b", "verification_reason": "c",
                "timestamp": "2026-01-01T00:00:00+00:00", "checks_evaluated": "[]",
            })


def test_overview_stats_counts_review_decisions_correctly(tmp_path):
    review_csv = tmp_path / "review_log.csv"
    _write_review_rows(review_csv, [
        ("Accepted", "C001"), ("Accepted", "C002"),
        ("Edited", "C003"), ("Rejected", "C004"), ("Rejected", "C005"),
    ])
    empty = str(tmp_path / "empty.csv")
    stats = dd.compute_overview_stats(review_csv=str(review_csv), ai_csv=empty, verification_csv=empty)
    assert stats["accepted_count"] == 2
    assert stats["edited_count"] == 1
    assert stats["rejected_count"] == 2


def test_overview_stats_counts_verification_outcomes_correctly(tmp_path):
    verification_csv = tmp_path / "verification_log.csv"
    _write_verification_rows(verification_csv, [
        ("Resolved", "C001"), ("Resolved", "C002"), ("Resolved", "C003"),
        ("Not Resolved", "C004"),
        ("Inconclusive", "C005"), ("Inconclusive", "C006"),
    ])
    empty = str(tmp_path / "empty.csv")
    stats = dd.compute_overview_stats(verification_csv=str(verification_csv), ai_csv=empty, review_csv=empty)
    assert stats["resolved_count"] == 3
    assert stats["not_resolved_count"] == 1
    assert stats["inconclusive_count"] == 2


def test_overview_stats_ignores_malformed_review_status(tmp_path):
    """A hand-edited/corrupted row with an invalid review_status must not
    crash the dashboard or be silently counted as a valid status."""
    review_csv = tmp_path / "review_log.csv"
    _write_review_rows(review_csv, [("Accepted", "C001")])
    # Append one malformed row directly.
    with open(review_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=review.CSV_FIELDNAMES)
        writer.writerow({
            "case_id": "C002", "ai_root_cause": "x", "ai_confidence": "50",
            "review_status": "Pending", "final_root_cause": "y",
            "reviewer_notes": "", "evidence_used": "[]",
            "timestamp": "2026-01-01T00:00:00+00:00",
        })
    empty = str(tmp_path / "empty.csv")
    stats = dd.compute_overview_stats(review_csv=str(review_csv), ai_csv=empty, verification_csv=empty)
    assert stats["accepted_count"] == 1
    # The malformed row shouldn't appear anywhere.
    assert sum(stats["review_status_counts"].values()) == 1


def test_overview_stats_ai_diagnoses_count_is_distinct_case_ids(tmp_path):
    """Diagnosing the same case twice (e.g. re-running for a demo)
    should count as 1 diagnosed case, not 2."""
    ai_csv = tmp_path / "ai_diagnoses.csv"
    fieldnames = [
        "case_id", "timestamp", "model", "root_cause", "confidence", "osi_layer",
        "observed_evidence", "inference", "next_command", "fix_steps",
        "human_review_required", "evidence_grounded", "ungrounded_evidence_items",
        "grounding_note", "checker_findings_count",
    ]
    with open(ai_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for ts in ("2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"):
            writer.writerow({k: "" for k in fieldnames} | {"case_id": "C001", "timestamp": ts})
        writer.writerow({k: "" for k in fieldnames} | {"case_id": "C002", "timestamp": "2026-01-01T00:00:00+00:00"})
    empty = str(tmp_path / "empty.csv")
    stats = dd.compute_overview_stats(ai_csv=str(ai_csv), review_csv=empty, verification_csv=empty)
    assert stats["ai_diagnoses_count"] == 2


# ---------------------------------------------------------------------------
# AI-human agreement
# ---------------------------------------------------------------------------

def test_ai_human_agreement_none_when_no_reviews(tmp_path):
    empty = str(tmp_path / "empty.csv")
    assert dd.compute_ai_human_agreement(review_csv=empty) is None


def test_ai_human_agreement_computes_correct_rate(tmp_path):
    review_csv = tmp_path / "review_log.csv"
    _write_review_rows(review_csv, [
        ("Accepted", "C001"), ("Accepted", "C002"), ("Accepted", "C003"),
        ("Edited", "C004"),
    ])
    result = dd.compute_ai_human_agreement(review_csv=str(review_csv))
    assert result["total_reviewed"] == 4
    assert result["accepted"] == 3
    assert result["corrected"] == 1
    assert result["agreement_rate"] == 0.75


# ---------------------------------------------------------------------------
# Responsible AI examples load correctly and stay labeled Evaluation/Demo
# ---------------------------------------------------------------------------

def test_load_responsible_ai_with_seeded_demo_examples(tmp_path):
    out_csv = str(tmp_path / "responsible_ai_log.csv")
    rai.seed_demo_examples(out_csv=out_csv)
    records = dd.load_responsible_ai(out_csv)
    assert len(records) == 5
    assert all(r["source_type"] == "Evaluation/Demo" for r in records)
