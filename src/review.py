#!/usr/bin/env python3
"""
review.py
---------
NetSage AI — Phase 3A: Human review workflow.

Every AI diagnosis produced by src/diagnosis.py is a recommendation only.
This module lets a human reviewer record a decision on it:

    Accepted  -- the AI's root cause is correct as-is
    Edited    -- the AI was on the right track but a detail (or the whole
                 root cause) needed correcting
    Rejected  -- the AI's root cause was wrong

Review records are stored in review_log.csv at the project root (the same
place ai_diagnoses.csv lives, following the existing project convention --
this project has no data/ directory, so one is not introduced here).

NetSage AI never modifies any Cisco configuration -- this module only
records a human decision in a CSV file. Nothing here connects to a device,
runs a command, or changes any config.

CLI usage:
    python review.py --case-id C001
        Interactive: prints the AI diagnosis on file (if any), then
        prompts for review_status / final_root_cause / reviewer_notes /
        evidence_used.

    python review.py --case-id C001 --status Edited \\
        --final-root-cause "Missing route" \\
        --reviewer-notes "Routing table lacked the destination network" \\
        --evidence "show ip route omits 192.168.30.0/24"
        Non-interactive, for scripting/demos/tests.

    python review.py --case-id C001 --list
        Show existing review records for a case (no input needed).

For automated tests, call submit_review(...) directly -- it never touches
stdin/stdout, so tests stay deterministic and don't depend on terminal
input.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diagnosis  # noqa: E402  (reuse load_case / CaseNotFoundError / paths)

PROJECT_ROOT = diagnosis.PROJECT_ROOT
REVIEW_LOG_CSV = os.path.join(PROJECT_ROOT, "review_log.csv")
AI_DIAGNOSES_CSV = diagnosis.AI_DIAGNOSES_CSV

VALID_STATUSES = ("Accepted", "Edited", "Rejected")

REQUIRED_RECORD_FIELDS = [
    "case_id", "ai_root_cause", "ai_confidence", "review_status",
    "final_root_cause", "reviewer_notes", "evidence_used", "timestamp",
]

CSV_FIELDNAMES = list(REQUIRED_RECORD_FIELDS)


# ---------------------------------------------------------------------------
# Exceptions -- one per failure mode, each with a clear, human-readable
# message. None of these should ever surface as a raw Python traceback to
# a normal user going through the CLI.
# ---------------------------------------------------------------------------

class ReviewError(Exception):
    """Base class for all human-review errors."""


class InvalidReviewStatusError(ReviewError):
    """Raised when review_status is not Accepted/Edited/Rejected."""


class MissingReviewerNotesError(ReviewError):
    """Raised when reviewer_notes is required (Edited/Rejected) but empty."""


class MissingFinalRootCauseError(ReviewError):
    """Raised when final_root_cause is required (Edited/Rejected) but empty."""


class AIRecordNotFoundError(ReviewError):
    """Raised when no AI diagnosis can be found for a case and none was
    supplied manually -- we never invent one."""


class MalformedAIRecordError(ReviewError):
    """Raised when an AI diagnosis record exists but is missing the
    fields a review needs from it (root_cause / confidence)."""


# ---------------------------------------------------------------------------
# Looking up the AI diagnosis being reviewed
# ---------------------------------------------------------------------------

def load_latest_ai_diagnosis(case_id: str, ai_csv: str = AI_DIAGNOSES_CSV) -> dict | None:
    """Return the most recent ai_diagnoses.csv row for case_id, or None if
    there isn't one (e.g. no AI diagnosis has been run for this case yet).
    Does not raise -- absence of a prior diagnosis is a normal, expected
    situation the caller decides how to handle."""
    if not os.path.exists(ai_csv):
        return None
    with open(ai_csv, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("case_id") == case_id]
    if not rows:
        return None
    return sorted(rows, key=lambda r: r.get("timestamp", ""))[-1]


def _validate_ai_record_shape(ai_record: dict, case_id: str) -> None:
    """A found AI record should at least have a root_cause and confidence
    to review against. If ai_diagnoses.csv got corrupted or hand-edited
    into a bad state, fail clearly rather than silently reviewing garbage."""
    if "root_cause" not in ai_record or ai_record.get("root_cause") in (None, ""):
        raise MalformedAIRecordError(
            f"The stored AI diagnosis for case {case_id!r} is missing a "
            f"'root_cause' value -- it looks corrupted. Re-run "
            f"'python diagnosis.py --case-id {case_id}' or supply "
            f"--ai-root-cause / --ai-confidence manually."
        )
    if "confidence" not in ai_record:
        raise MalformedAIRecordError(
            f"The stored AI diagnosis for case {case_id!r} is missing a "
            f"'confidence' value -- it looks corrupted."
        )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_review_record(data: dict) -> dict:
    """Validate one review record dict against the Phase 3A rules:

      - review_status must be exactly Accepted / Edited / Rejected.
      - Accepted: final_root_cause defaults to the AI's root cause if not
        given; reviewer_notes is supported but optional.
      - Edited: final_root_cause is required (non-empty); reviewer_notes
        is required (must explain the change).
      - Rejected: final_root_cause is required (the corrected diagnosis,
        or a clear statement that it's still unknown); reviewer_notes is
        required (must explain why the AI was wrong).

    Raises InvalidReviewStatusError, MissingFinalRootCauseError, or
    MissingReviewerNotesError as appropriate. Returns a normalized copy
    (evidence_used coerced to a list, whitespace stripped).
    """
    status = data.get("review_status")
    if status not in VALID_STATUSES:
        raise InvalidReviewStatusError(
            f"review_status must be one of {VALID_STATUSES}, got {status!r}"
        )

    final_root_cause = (data.get("final_root_cause") or "").strip()
    reviewer_notes = (data.get("reviewer_notes") or "").strip()

    if status == "Accepted":
        # final_root_cause may default to the AI's root cause.
        if not final_root_cause:
            final_root_cause = (data.get("ai_root_cause") or "").strip()
        # reviewer_notes is supported but not required.

    elif status == "Edited":
        if not final_root_cause:
            raise MissingFinalRootCauseError(
                "review_status is 'Edited' but no final_root_cause was "
                "given -- an edit must state the corrected root cause."
            )
        if not reviewer_notes:
            raise MissingReviewerNotesError(
                "review_status is 'Edited' but no reviewer_notes were "
                "given -- explain what changed and why."
            )

    elif status == "Rejected":
        if not final_root_cause:
            raise MissingFinalRootCauseError(
                "review_status is 'Rejected' but no final_root_cause was "
                "given -- state what the correct diagnosis should be (or "
                "that it is still unknown and needs further investigation)."
            )
        if not reviewer_notes:
            raise MissingReviewerNotesError(
                "review_status is 'Rejected' but no reviewer_notes were "
                "given -- explain why the AI's diagnosis was wrong."
            )

    evidence = data.get("evidence_used", [])
    if isinstance(evidence, str):
        evidence = [e.strip() for e in evidence.split(";") if e.strip()]
    if not isinstance(evidence, list):
        raise ReviewError(
            "'evidence_used' must be a list of strings, or a ';'-separated string"
        )

    normalized = dict(data)
    normalized["final_root_cause"] = final_root_cause
    normalized["reviewer_notes"] = reviewer_notes
    normalized["evidence_used"] = evidence
    return normalized


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def save_review_record(record: dict, out_csv: str = REVIEW_LOG_CSV) -> None:
    """Append one review record to review_log.csv, writing the header row
    first if the file doesn't exist yet."""
    file_exists = os.path.exists(out_csv)
    row = dict(record)
    row["evidence_used"] = json.dumps(record.get("evidence_used", []))
    with open(out_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in CSV_FIELDNAMES})


def list_reviews(case_id: str | None = None, csv_path: str = REVIEW_LOG_CSV) -> list[dict]:
    """Return review records, optionally filtered to one case_id. Returns
    an empty list (not an error) if the log doesn't exist yet."""
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if case_id:
        rows = [r for r in rows if r.get("case_id") == case_id]
    return rows


# ---------------------------------------------------------------------------
# Top-level orchestration (used by both the CLI and the tests -- this is
# the "non-interactive/testable function" the spec asks for)
# ---------------------------------------------------------------------------

def submit_review(
    case_id: str,
    review_status: str,
    final_root_cause: str = "",
    reviewer_notes: str = "",
    evidence_used: list[str] | str | None = None,
    ai_root_cause: str | None = None,
    ai_confidence: Any = None,
    timestamp: str | None = None,
    cases_csv: str = None,
    ai_csv: str = AI_DIAGNOSES_CSV,
    out_csv: str = REVIEW_LOG_CSV,
) -> dict:
    """Load the case + its AI diagnosis, validate the review, save it, and
    return the normalized record.

    Raises:
        diagnosis.CaseNotFoundError  -- case_id doesn't exist in cases.csv
        AIRecordNotFoundError        -- no AI diagnosis on file and none
                                         supplied manually via ai_root_cause/
                                         ai_confidence
        MalformedAIRecordError       -- a stored AI record is missing
                                         fields a review needs
        InvalidReviewStatusError     -- review_status isn't one of the 3
        MissingFinalRootCauseError   -- required final_root_cause missing
        MissingReviewerNotesError    -- required reviewer_notes missing
    """
    cases_csv = cases_csv or diagnosis.CASES_CSV

    # Confirms the case exists; reuses Phase 2's lookup + its
    # CaseNotFoundError, so "nonexistent case ID" gets one consistent,
    # clear error across the whole project.
    diagnosis.load_case(case_id, cases_csv)

    if ai_root_cause is None or ai_confidence is None:
        ai_record = load_latest_ai_diagnosis(case_id, ai_csv)
        if ai_record is None:
            raise AIRecordNotFoundError(
                f"No AI diagnosis found for case {case_id!r} in {ai_csv}, and "
                f"none was supplied manually. Run 'python diagnosis.py "
                f"--case-id {case_id}' first, or pass ai_root_cause / "
                f"ai_confidence explicitly."
            )
        _validate_ai_record_shape(ai_record, case_id)
        if ai_root_cause is None:
            ai_root_cause = ai_record.get("root_cause", "")
        if ai_confidence is None:
            ai_confidence = ai_record.get("confidence", "")

    raw = {
        "case_id": case_id,
        "ai_root_cause": ai_root_cause,
        "ai_confidence": ai_confidence,
        "review_status": review_status,
        "final_root_cause": final_root_cause,
        "reviewer_notes": reviewer_notes,
        "evidence_used": evidence_used if evidence_used is not None else [],
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
    }
    normalized = validate_review_record(raw)
    save_review_record(normalized, out_csv)
    return normalized


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_ai_diagnosis(case_id: str, ai_record: dict | None) -> None:
    print(f"\n=== Case {case_id} ===")
    if ai_record is None:
        print("No AI diagnosis on file for this case yet.")
        print(f"(Run: python diagnosis.py --case-id {case_id})")
        return
    print(f"AI root cause:     {ai_record.get('root_cause', '')}")
    print(f"AI confidence:      {ai_record.get('confidence', '')}/100")
    print(f"OSI layer:          {ai_record.get('osi_layer', '')}")
    print(f"Evidence grounded:  {ai_record.get('evidence_grounded', '')}")


def _prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{text}{suffix}: ").strip()
    return value or default


def main() -> int:
    parser = argparse.ArgumentParser(description="NetSage AI Phase 3A -- human review of an AI diagnosis")
    parser.add_argument("--case-id", required=True, help="e.g. C001")
    parser.add_argument("--status", choices=VALID_STATUSES, default=None,
                         help="Accepted / Edited / Rejected. Omit for interactive prompts.")
    parser.add_argument("--final-root-cause", default=None)
    parser.add_argument("--reviewer-notes", default=None)
    parser.add_argument("--evidence", nargs="*", default=None, help="One or more evidence strings")
    parser.add_argument("--ai-root-cause", default=None, help="Override/supply the AI root cause manually")
    parser.add_argument("--ai-confidence", default=None, help="Override/supply the AI confidence manually")
    parser.add_argument("--list", action="store_true", help="List existing reviews for this case and exit")
    parser.add_argument("--out", default=REVIEW_LOG_CSV)
    args = parser.parse_args()

    if args.list:
        rows = list_reviews(args.case_id, args.out)
        if not rows:
            print(f"No review records found for {args.case_id}.")
            return 0
        for r in rows:
            print(f"[{r['timestamp']}] {r['review_status']}: {r['final_root_cause']}")
        return 0

    # 1. Load the case (clear error, no traceback, for a bad/missing case ID).
    try:
        diagnosis.load_case(args.case_id)
    except diagnosis.CaseNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # 2. Load the corresponding AI diagnosis (may legitimately be None).
    ai_record = load_latest_ai_diagnosis(args.case_id)

    # 3. Display the diagnosis clearly.
    _print_ai_diagnosis(args.case_id, ai_record)

    status = args.status
    final_root_cause = args.final_root_cause
    reviewer_notes = args.reviewer_notes
    evidence = args.evidence

    if status is None:
        # 4-5. Interactive prompts.
        status = _prompt(f"Review decision ({'/'.join(VALID_STATUSES)})")
        default_final = ai_record.get("root_cause", "") if ai_record else ""
        final_root_cause = _prompt("Final root cause", default=default_final)
        reviewer_notes = _prompt("Reviewer notes")
        evidence_raw = _prompt("Evidence used (separate multiple with ;)")
        evidence = [e.strip() for e in evidence_raw.split(";") if e.strip()]
    else:
        evidence = evidence or []

    # 6. Save the review record.
    try:
        result = submit_review(
            args.case_id,
            review_status=status,
            final_root_cause=final_root_cause or "",
            reviewer_notes=reviewer_notes or "",
            evidence_used=evidence or [],
            ai_root_cause=args.ai_root_cause,
            ai_confidence=args.ai_confidence,
            out_csv=args.out,
        )
    except (AIRecordNotFoundError, MalformedAIRecordError,
            InvalidReviewStatusError, MissingFinalRootCauseError,
            MissingReviewerNotesError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # 7. Print a confirmation.
    print(f"\nSaved review: {result['review_status']} -- {result['final_root_cause']}")
    print(f"Written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
