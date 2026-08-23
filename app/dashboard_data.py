#!/usr/bin/env python3
"""
dashboard_data.py
------------------
NetSage AI — Phase 4: dashboard data layer.

This module contains ONLY plain Python: loading CSVs and computing
statistics from them. It has no Streamlit dependency, which makes it
directly unit-testable (see tests/test_dashboard_data.py) without needing
a running Streamlit session.

It deliberately reuses the existing Phase 1-3 modules (diagnosis, review,
verify, responsible_ai) for path constants and CSV-reading logic rather
than re-parsing anything itself -- this is the same project-relative-path
convention every other module already follows, and it means the
dashboard can never drift out of sync with how those modules actually
store data.

Every "load_*" function returns an empty list if the underlying CSV
doesn't exist yet or is empty -- never fabricated rows. Every "compute_*"
function only counts values it recognizes as valid (Accepted/Edited/
Rejected, Resolved/Not Resolved/Inconclusive, etc.), so a malformed or
hand-edited CSV row is silently skipped rather than crashing the
dashboard or being counted as something it isn't.
"""

from __future__ import annotations

import csv
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import diagnosis  # noqa: E402
import review  # noqa: E402
import verify  # noqa: E402
import responsible_ai as rai  # noqa: E402

PROJECT_ROOT = diagnosis.PROJECT_ROOT
CASES_CSV = diagnosis.CASES_CSV
AI_DIAGNOSES_CSV = diagnosis.AI_DIAGNOSES_CSV
REVIEW_LOG_CSV = review.REVIEW_LOG_CSV
VERIFICATION_LOG_CSV = verify.VERIFICATION_LOG_CSV
RESPONSIBLE_AI_LOG_CSV = rai.RESPONSIBLE_AI_LOG_CSV


# ---------------------------------------------------------------------------
# Loading (each returns [] if the file doesn't exist yet -- this is the
# "No data available yet" signal the dashboard checks for)
# ---------------------------------------------------------------------------

def load_cases(cases_csv: str = CASES_CSV) -> list[dict]:
    if not os.path.exists(cases_csv):
        return []
    with open(cases_csv, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_ai_diagnoses(csv_path: str = AI_DIAGNOSES_CSV) -> list[dict]:
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_reviews(csv_path: str = REVIEW_LOG_CSV) -> list[dict]:
    return review.list_reviews(csv_path=csv_path)


def load_verifications(csv_path: str = VERIFICATION_LOG_CSV) -> list[dict]:
    return verify.list_verifications(csv_path=csv_path)


def load_responsible_ai(csv_path: str = RESPONSIBLE_AI_LOG_CSV) -> list[dict]:
    return rai.list_records(csv_path=csv_path)


def _latest_by_timestamp(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    return sorted(rows, key=lambda r: r.get("timestamp", ""))[-1]


# ---------------------------------------------------------------------------
# Case bundle (used by the Case Explorer)
# ---------------------------------------------------------------------------

def get_case_bundle(
    case_id: str,
    cases_csv: str = CASES_CSV,
    ai_csv: str = AI_DIAGNOSES_CSV,
    review_csv: str = REVIEW_LOG_CSV,
    verification_csv: str = VERIFICATION_LOG_CSV,
) -> dict | None:
    """Gather everything known about one case: the case itself, its
    latest AI diagnosis (if any), latest human review (if any), and
    latest verification (if any). Returns None if case_id doesn't exist.
    Each of ai_diagnosis/review/verification is None if that step
    hasn't happened for this case yet -- never fabricated."""
    try:
        case = diagnosis.load_case(case_id, cases_csv)
    except diagnosis.CaseNotFoundError:
        return None

    ai_rows = [r for r in load_ai_diagnoses(ai_csv) if r.get("case_id") == case_id]
    review_rows = load_reviews(review_csv)
    review_rows = [r for r in review_rows if r.get("case_id") == case_id]
    verification_rows = load_verifications(verification_csv)
    verification_rows = [r for r in verification_rows if r.get("case_id") == case_id]

    return {
        "case": case,
        "ai_diagnosis": _latest_by_timestamp(ai_rows),
        "review": _latest_by_timestamp(review_rows),
        "verification": _latest_by_timestamp(verification_rows),
    }


# ---------------------------------------------------------------------------
# Overview statistics (all computed, nothing hardcoded)
# ---------------------------------------------------------------------------

def compute_overview_stats(
    cases: list[dict] | None = None,
    ai_diagnoses: list[dict] | None = None,
    reviews: list[dict] | None = None,
    verifications: list[dict] | None = None,
    cases_csv: str = CASES_CSV,
    ai_csv: str = AI_DIAGNOSES_CSV,
    review_csv: str = REVIEW_LOG_CSV,
    verification_csv: str = VERIFICATION_LOG_CSV,
) -> dict:
    """Compute every number the Overview section needs, purely from
    stored CSV data. Rows with a status value outside the known valid
    set (a malformed/hand-edited row) are silently excluded from counts
    rather than crashing or being miscounted."""
    cases = load_cases(cases_csv) if cases is None else cases
    ai_diagnoses = load_ai_diagnoses(ai_csv) if ai_diagnoses is None else ai_diagnoses
    reviews = load_reviews(review_csv) if reviews is None else reviews
    verifications = load_verifications(verification_csv) if verifications is None else verifications

    ai_diagnosed_case_ids = {r.get("case_id") for r in ai_diagnoses if r.get("case_id")}

    review_status_counts = Counter(
        r.get("review_status") for r in reviews if r.get("review_status") in review.VALID_STATUSES
    )
    verification_status_counts = Counter(
        r.get("verification_status") for r in verifications
        if r.get("verification_status") in verify.VALID_STATUSES
    )
    category_counts = Counter(c.get("category", "Unknown") or "Unknown" for c in cases)
    severity_counts = Counter(c.get("severity", "Unknown") or "Unknown" for c in cases)

    return {
        "total_cases": len(cases),
        "ai_diagnoses_count": len(ai_diagnosed_case_ids),
        "accepted_count": review_status_counts.get("Accepted", 0),
        "edited_count": review_status_counts.get("Edited", 0),
        "rejected_count": review_status_counts.get("Rejected", 0),
        "resolved_count": verification_status_counts.get("Resolved", 0),
        "not_resolved_count": verification_status_counts.get("Not Resolved", 0),
        "inconclusive_count": verification_status_counts.get("Inconclusive", 0),
        "category_counts": dict(category_counts),
        "severity_counts": dict(severity_counts),
        "review_status_counts": dict(review_status_counts),
        "verification_status_counts": dict(verification_status_counts),
    }


def compute_ai_human_agreement(reviews: list[dict] | None = None, review_csv: str = REVIEW_LOG_CSV) -> dict | None:
    """What fraction of reviewed diagnoses were Accepted as-is (i.e. the
    AI and the human agreed) vs. needed a correction. Returns None if
    there are no reviews yet -- there's nothing to compute agreement on."""
    reviews = load_reviews(review_csv) if reviews is None else reviews
    valid = [r for r in reviews if r.get("review_status") in review.VALID_STATUSES]
    if not valid:
        return None
    accepted = sum(1 for r in valid if r["review_status"] == "Accepted")
    corrected = sum(1 for r in valid if r["review_status"] in ("Edited", "Rejected"))
    return {
        "total_reviewed": len(valid),
        "accepted": accepted,
        "corrected": corrected,
        "agreement_rate": round(accepted / len(valid), 3),
    }
