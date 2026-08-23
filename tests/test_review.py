"""
test_review.py
---------------
Phase 3A tests for src/review.py: Accepted / Edited / Rejected reviews,
validation of required fields, invalid status handling, invalid/missing
case IDs, missing AI diagnosis handling, CSV persistence, and loading
existing reviews back.

No test depends on terminal input -- everything goes through
submit_review() directly, which is the "non-interactive/testable
function" the spec calls for.

Run from the project root:
    python3 -m pytest tests/test_review.py -v
"""

import csv
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import review  # noqa: E402
import diagnosis  # noqa: E402


# A fake-but-well-formed AI diagnosis row, as it would appear in
# ai_diagnoses.csv, used to seed a temp CSV for tests.
FAKE_AI_ROW = {
    "case_id": "C007",
    "timestamp": "2026-01-01T00:00:00+00:00",
    "model": "claude-sonnet-4-5",
    "root_cause": "PC2's default gateway does not match VLAN 10's router interface.",
    "confidence": "90",
    "osi_layer": "Layer 3",
    "observed_evidence": json.dumps(["ipconfig shows Default Gateway: 192.168.1.1"]),
    "inference": "The gateway is outside PC2's subnet.",
    "next_command": "",
    "fix_steps": json.dumps(["Set PC2's default gateway to 192.168.10.1"]),
    "human_review_required": "True",
    "evidence_grounded": "True",
    "ungrounded_evidence_items": "[]",
    "grounding_note": "All observed_evidence items are grounded.",
    "checker_findings_count": "1",
}


@pytest.fixture
def ai_csv(tmp_path):
    """A temp ai_diagnoses.csv with one row for case C007."""
    path = tmp_path / "ai_diagnoses.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(FAKE_AI_ROW.keys()))
        writer.writeheader()
        writer.writerow(FAKE_AI_ROW)
    return str(path)


@pytest.fixture
def review_csv(tmp_path):
    return str(tmp_path / "review_log.csv")


# ---------------------------------------------------------------------------
# 1. Accepted review
# ---------------------------------------------------------------------------

def test_accepted_review_saves_correctly(ai_csv, review_csv):
    result = review.submit_review(
        "C007",
        review_status="Accepted",
        reviewer_notes="Checked evidence, AI got it right.",
        ai_csv=ai_csv,
        out_csv=review_csv,
    )
    assert result["review_status"] == "Accepted"
    # final_root_cause should default to the AI's root cause.
    assert result["final_root_cause"] == FAKE_AI_ROW["root_cause"]
    assert os.path.exists(review_csv)


def test_accepted_review_does_not_require_reviewer_notes(ai_csv, review_csv):
    # "reviewer notes should still be supported" (not required) for Accepted.
    result = review.submit_review(
        "C007", review_status="Accepted", ai_csv=ai_csv, out_csv=review_csv,
    )
    assert result["review_status"] == "Accepted"
    assert result["reviewer_notes"] == ""


def test_accepted_review_allows_explicit_final_root_cause_override(ai_csv, review_csv):
    result = review.submit_review(
        "C007", review_status="Accepted",
        final_root_cause="PC2's gateway does not match VLAN 10's router interface (confirmed).",
        ai_csv=ai_csv, out_csv=review_csv,
    )
    assert "(confirmed)" in result["final_root_cause"]


# ---------------------------------------------------------------------------
# 2. Edited review
# ---------------------------------------------------------------------------

def test_edited_review_saves_correctly(ai_csv, review_csv):
    result = review.submit_review(
        "C007",
        review_status="Edited",
        final_root_cause="Gateway mismatch, but also DNS server was stale.",
        reviewer_notes="AI missed the secondary DNS issue.",
        evidence_used=["ipconfig shows DNS Servers: 203.0.113.9"],
        ai_csv=ai_csv, out_csv=review_csv,
    )
    assert result["review_status"] == "Edited"
    assert result["final_root_cause"] == "Gateway mismatch, but also DNS server was stale."
    assert result["reviewer_notes"] == "AI missed the secondary DNS issue."


def test_edited_review_requires_final_root_cause(ai_csv, review_csv):
    with pytest.raises(review.MissingFinalRootCauseError):
        review.submit_review(
            "C007", review_status="Edited", reviewer_notes="Something changed",
            ai_csv=ai_csv, out_csv=review_csv,
        )


def test_edited_review_requires_reviewer_notes(ai_csv, review_csv):
    with pytest.raises(review.MissingReviewerNotesError):
        review.submit_review(
            "C007", review_status="Edited", final_root_cause="Corrected cause",
            ai_csv=ai_csv, out_csv=review_csv,
        )


# ---------------------------------------------------------------------------
# 3. Rejected review
# ---------------------------------------------------------------------------

def test_rejected_review_saves_correctly(ai_csv, review_csv):
    result = review.submit_review(
        "C007",
        review_status="Rejected",
        final_root_cause="Actually an ACL was blocking the traffic, not a gateway issue.",
        reviewer_notes="AI misread the evidence; no gateway mismatch actually exists here.",
        ai_csv=ai_csv, out_csv=review_csv,
    )
    assert result["review_status"] == "Rejected"
    assert "ACL" in result["final_root_cause"]


def test_rejected_review_requires_final_root_cause(ai_csv, review_csv):
    with pytest.raises(review.MissingFinalRootCauseError):
        review.submit_review(
            "C007", review_status="Rejected", reviewer_notes="AI was wrong",
            ai_csv=ai_csv, out_csv=review_csv,
        )


def test_rejected_review_requires_reviewer_notes(ai_csv, review_csv):
    with pytest.raises(review.MissingReviewerNotesError):
        review.submit_review(
            "C007", review_status="Rejected", final_root_cause="Something else entirely",
            ai_csv=ai_csv, out_csv=review_csv,
        )


# ---------------------------------------------------------------------------
# 4. Invalid status
# ---------------------------------------------------------------------------

def test_invalid_review_status_raises(ai_csv, review_csv):
    with pytest.raises(review.InvalidReviewStatusError):
        review.submit_review(
            "C007", review_status="Maybe", reviewer_notes="unsure",
            ai_csv=ai_csv, out_csv=review_csv,
        )


def test_invalid_review_status_rejects_lowercase_variant(ai_csv, review_csv):
    # Status matching must be exact -- "accepted" is not "Accepted".
    with pytest.raises(review.InvalidReviewStatusError):
        review.submit_review(
            "C007", review_status="accepted",
            ai_csv=ai_csv, out_csv=review_csv,
        )


def test_validate_review_record_directly_rejects_bad_status():
    with pytest.raises(review.InvalidReviewStatusError):
        review.validate_review_record({"review_status": "Pending"})


# ---------------------------------------------------------------------------
# 5. Missing reviewer notes (covered above for Edited/Rejected; also test
#    the pure validation function directly)
# ---------------------------------------------------------------------------

def test_validate_review_record_missing_notes_for_edited():
    with pytest.raises(review.MissingReviewerNotesError):
        review.validate_review_record({
            "review_status": "Edited",
            "final_root_cause": "Something",
            "reviewer_notes": "",
        })


def test_validate_review_record_missing_notes_for_rejected():
    with pytest.raises(review.MissingReviewerNotesError):
        review.validate_review_record({
            "review_status": "Rejected",
            "final_root_cause": "Something else",
            "reviewer_notes": "   ",  # whitespace-only counts as missing
        })


# ---------------------------------------------------------------------------
# 6. Missing final diagnosis (final_root_cause)
# ---------------------------------------------------------------------------

def test_validate_review_record_missing_final_root_cause_for_edited():
    with pytest.raises(review.MissingFinalRootCauseError):
        review.validate_review_record({
            "review_status": "Edited",
            "final_root_cause": "",
            "reviewer_notes": "explanation here",
        })


def test_validate_review_record_missing_final_root_cause_for_rejected():
    with pytest.raises(review.MissingFinalRootCauseError):
        review.validate_review_record({
            "review_status": "Rejected",
            "final_root_cause": "   ",
            "reviewer_notes": "explanation here",
        })


def test_accepted_defaults_final_root_cause_when_missing():
    normalized = review.validate_review_record({
        "review_status": "Accepted",
        "ai_root_cause": "AI's original root cause",
        "final_root_cause": "",
        "reviewer_notes": "",
    })
    assert normalized["final_root_cause"] == "AI's original root cause"


# ---------------------------------------------------------------------------
# 7. Invalid case ID
# ---------------------------------------------------------------------------

def test_submit_review_invalid_case_id_raises(ai_csv, review_csv):
    with pytest.raises(diagnosis.CaseNotFoundError):
        review.submit_review(
            "C999", review_status="Accepted",
            ai_csv=ai_csv, out_csv=review_csv,
        )


def test_submit_review_invalid_case_id_message_is_clear(ai_csv, review_csv):
    with pytest.raises(diagnosis.CaseNotFoundError, match="C999"):
        review.submit_review(
            "C999", review_status="Accepted",
            ai_csv=ai_csv, out_csv=review_csv,
        )


# ---------------------------------------------------------------------------
# 8. Missing AI diagnosis
# ---------------------------------------------------------------------------

def test_submit_review_missing_ai_diagnosis_raises(review_csv, tmp_path):
    empty_ai_csv = str(tmp_path / "empty_ai_diagnoses.csv")  # doesn't exist
    with pytest.raises(review.AIRecordNotFoundError):
        review.submit_review(
            "C007", review_status="Accepted",
            ai_csv=empty_ai_csv, out_csv=review_csv,
        )


def test_submit_review_missing_ai_diagnosis_can_be_supplied_manually(review_csv, tmp_path):
    empty_ai_csv = str(tmp_path / "empty_ai_diagnoses.csv")
    result = review.submit_review(
        "C007", review_status="Accepted",
        ai_root_cause="Manually supplied root cause",
        ai_confidence=75,
        ai_csv=empty_ai_csv, out_csv=review_csv,
    )
    assert result["ai_root_cause"] == "Manually supplied root cause"
    assert result["final_root_cause"] == "Manually supplied root cause"


def test_submit_review_malformed_ai_record_raises(review_csv, tmp_path):
    """A row exists for the case but is missing 'root_cause' -- this
    should be treated as corrupted data, not silently reviewed."""
    bad_ai_csv = tmp_path / "bad_ai_diagnoses.csv"
    bad_row = dict(FAKE_AI_ROW)
    del bad_row["root_cause"]
    with open(bad_ai_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(bad_row.keys()))
        writer.writeheader()
        writer.writerow(bad_row)

    with pytest.raises(review.MalformedAIRecordError):
        review.submit_review(
            "C007", review_status="Accepted",
            ai_csv=str(bad_ai_csv), out_csv=review_csv,
        )


# ---------------------------------------------------------------------------
# 9. Successful persistence to CSV
# ---------------------------------------------------------------------------

def test_review_persists_all_required_fields_to_csv(ai_csv, review_csv):
    review.submit_review(
        "C007", review_status="Edited",
        final_root_cause="Corrected cause",
        reviewer_notes="Because reasons",
        evidence_used=["evidence line 1", "evidence line 2"],
        ai_csv=ai_csv, out_csv=review_csv,
    )
    assert os.path.exists(review_csv)
    with open(review_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    row = rows[0]
    for field in review.REQUIRED_RECORD_FIELDS:
        assert field in row
    assert row["case_id"] == "C007"
    assert row["review_status"] == "Edited"
    assert json.loads(row["evidence_used"]) == ["evidence line 1", "evidence line 2"]


def test_multiple_reviews_append_rather_than_overwrite(ai_csv, review_csv):
    review.submit_review(
        "C007", review_status="Accepted", ai_csv=ai_csv, out_csv=review_csv,
    )
    review.submit_review(
        "C007", review_status="Edited",
        final_root_cause="Refined cause",
        reviewer_notes="Second pass",
        ai_csv=ai_csv, out_csv=review_csv,
    )
    with open(review_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# 10. Loading an existing review
# ---------------------------------------------------------------------------

def test_list_reviews_returns_saved_record(ai_csv, review_csv):
    review.submit_review(
        "C007", review_status="Rejected",
        final_root_cause="Different cause entirely",
        reviewer_notes="AI was wrong about the layer",
        ai_csv=ai_csv, out_csv=review_csv,
    )
    rows = review.list_reviews("C007", csv_path=review_csv)
    assert len(rows) == 1
    assert rows[0]["review_status"] == "Rejected"


def test_list_reviews_filters_by_case_id(ai_csv, review_csv):
    review.submit_review("C007", review_status="Accepted", ai_csv=ai_csv, out_csv=review_csv)
    # Manually add a second case's row so we can confirm filtering works.
    with open(review_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=review.CSV_FIELDNAMES)
        writer.writerow({
            "case_id": "C001", "ai_root_cause": "x", "ai_confidence": "50",
            "review_status": "Accepted", "final_root_cause": "x",
            "reviewer_notes": "", "evidence_used": "[]",
            "timestamp": "2026-01-01T00:00:00+00:00",
        })
    all_rows = review.list_reviews(csv_path=review_csv)
    c007_rows = review.list_reviews("C007", csv_path=review_csv)
    assert len(all_rows) == 2
    assert len(c007_rows) == 1
    assert c007_rows[0]["case_id"] == "C007"


def test_list_reviews_returns_empty_list_when_no_log_exists(tmp_path):
    nonexistent = str(tmp_path / "does_not_exist.csv")
    assert review.list_reviews("C007", csv_path=nonexistent) == []


# ---------------------------------------------------------------------------
# Safety: review never touches network/device configuration
# ---------------------------------------------------------------------------

def test_review_module_has_no_device_automation_capability():
    """Sanity check that review.py cannot push configuration to a device --
    it should only read CSVs and write CSVs. This is a lightweight proxy
    for 'no automatic configuration changes are performed', appropriate
    for Phase 3A's scope (review recording only)."""
    forbidden_terms = ["paramiko", "netmiko", "telnetlib", "subprocess",
                        "os.system", "napalm", "ncclient"]
    src_path = os.path.join(os.path.dirname(__file__), "..", "src", "review.py")
    with open(src_path, encoding="utf-8") as f:
        source = f.read()
    for term in forbidden_terms:
        assert term not in source, f"review.py unexpectedly references {term!r}"
