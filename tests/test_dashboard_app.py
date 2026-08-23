"""
test_dashboard_app.py
-----------------------
Phase 4 lightweight smoke tests for app/dashboard.py.

Per the assignment, this does NOT test every visual element -- it uses
Streamlit's headless AppTest harness to confirm the dashboard actually
runs end-to-end without raising an exception, in two states:

  1. Empty state -- only cases.csv exists (the real state of a fresh
     checkout before anyone has run diagnosis/review/verify).
  2. Full-data state -- ai_diagnoses.csv, review_log.csv,
     verification_log.csv, and responsible_ai_log.csv all have data.

Any CSVs created for the full-data test are written to the real project
root (dashboard.py uses fixed default paths, same as every other module
in this project) and are removed again at the end of the test, so the
repository is left clean either way.

Run from the project root:
    python3 -m pytest tests/test_dashboard_app.py -v
"""

import csv
import json
import os
import sys

import pytest
from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import diagnosis  # noqa: E402
import review  # noqa: E402
import verify  # noqa: E402
import responsible_ai as rai  # noqa: E402

DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), "..", "app", "dashboard.py")


def test_dashboard_loads_in_empty_state():
    """With no ai_diagnoses.csv / review_log.csv / verification_log.csv /
    responsible_ai_log.csv on disk, the dashboard must still load cleanly
    and show 'no data yet' states instead of crashing."""
    # Guard: fail loudly if a real run has left data behind, since that
    # would make this "empty state" test misleading rather than fixing it
    # silently.
    for path in (diagnosis.AI_DIAGNOSES_CSV, review.REVIEW_LOG_CSV,
                 verify.VERIFICATION_LOG_CSV, rai.RESPONSIBLE_AI_LOG_CSV):
        assert not os.path.exists(path), (
            f"{path} unexpectedly exists -- this test assumes a clean "
            f"project state. Remove it before running tests."
        )

    at = AppTest.from_file(DASHBOARD_PATH, default_timeout=30)
    at.run()
    assert not at.exception, f"Dashboard raised an exception in empty state: {at.exception}"


@pytest.fixture
def seeded_project_csvs():
    """Temporarily populate the real project-root CSVs with minimal valid
    data so the full-data path can be exercised, then clean up
    afterward regardless of test outcome."""
    ai_fieldnames = [
        "case_id", "timestamp", "model", "root_cause", "confidence", "osi_layer",
        "observed_evidence", "inference", "next_command", "fix_steps",
        "human_review_required", "evidence_grounded", "ungrounded_evidence_items",
        "grounding_note", "checker_findings_count",
    ]
    with open(diagnosis.AI_DIAGNOSES_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ai_fieldnames)
        writer.writeheader()
        writer.writerow({
            "case_id": "C007", "timestamp": "2026-01-01T00:00:00+00:00",
            "model": "claude-sonnet-4-5",
            "root_cause": "PC2's default gateway does not match VLAN 10's router interface.",
            "confidence": "90", "osi_layer": "Layer 3",
            "observed_evidence": json.dumps(["ipconfig shows Default Gateway: 192.168.1.1"]),
            "inference": "The gateway is outside PC2's subnet.",
            "next_command": "", "fix_steps": json.dumps(["Set PC2's default gateway to 192.168.10.1"]),
            "human_review_required": "True", "evidence_grounded": "True",
            "ungrounded_evidence_items": "[]",
            "grounding_note": "All observed_evidence items are grounded.",
            "checker_findings_count": "1",
        })

    with open(review.REVIEW_LOG_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=review.CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerow({
            "case_id": "C007", "ai_root_cause": "PC2's default gateway does not match VLAN 10's router interface.",
            "ai_confidence": "90", "review_status": "Accepted",
            "final_root_cause": "PC2's default gateway does not match VLAN 10's router interface.",
            "reviewer_notes": "", "evidence_used": "[]",
            "timestamp": "2026-01-01T00:00:00+00:00",
        })

    with open(verify.VERIFICATION_LOG_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=verify.CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerow({
            "case_id": "C007", "final_diagnosis": "Gateway mismatch",
            "verification_status": "Resolved",
            "before_evidence": "Default Gateway: 192.168.1.1",
            "after_evidence": "Default Gateway: 192.168.10.1",
            "verification_reason": "Expected gateway now appears in the after-evidence.",
            "timestamp": "2026-01-01T00:00:00+00:00", "checks_evaluated": json.dumps(["gateway_mismatch"]),
        })

    rai.seed_demo_examples(out_csv=rai.RESPONSIBLE_AI_LOG_CSV)

    yield

    for path in (diagnosis.AI_DIAGNOSES_CSV, review.REVIEW_LOG_CSV,
                 verify.VERIFICATION_LOG_CSV, rai.RESPONSIBLE_AI_LOG_CSV):
        if os.path.exists(path):
            os.remove(path)


def test_dashboard_loads_in_full_data_state(seeded_project_csvs):
    """With real data present in every CSV, the dashboard must still
    load without raising -- this exercises every render_* branch that
    the empty-state test skips over via 'no data yet' messages."""
    at = AppTest.from_file(DASHBOARD_PATH, default_timeout=30)
    at.run()
    assert not at.exception, f"Dashboard raised an exception with full data: {at.exception}"
