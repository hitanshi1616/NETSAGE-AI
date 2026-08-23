#!/usr/bin/env python3
"""
responsible_ai.py
------------------
NetSage AI — Phase 3C: Responsible AI logging.

The Cisco problem statement requires documenting at least 5 cases where
the AI diagnosis was corrected by a human. This module keeps that log
clean and auditable, and -- critically -- never blurs the line between:

    1. an actual AI diagnosis result (from src/diagnosis.py calling the
       real Anthropic API)
    2. a human correction of that result (from src/review.py)
    3. an evaluation/demo example built to illustrate what a correction
       looks like, when no real API run for that case exists yet

Every record's `source_type` field says explicitly which of these it is.
This module NEVER writes "Real AI Run" for a record unless the AI root
cause actually came from ai_diagnoses.csv (i.e. a genuine API call). The
5 seeded examples in this file are clearly and permanently labeled
"Evaluation/Demo" -- they illustrate realistic failure patterns using
each case's real show-command evidence, but the "AI diagnosis" text in
them was written by hand as a plausible example, not produced by an
actual model call.

Records are stored in responsible_ai_log.csv at the project root,
following this project's existing convention (ai_diagnoses.csv,
review_log.csv, and verification_log.csv all live there too).

CLI usage:
    python responsible_ai.py --list
    python responsible_ai.py --list --case-id C002

    python responsible_ai.py --add --case-id C002 \\
        --source-type "Evaluation/Demo" \\
        --ai-root-cause "..." --human-decision Rejected \\
        --human-corrected-root-cause "..." \\
        --correction-reason "..." \\
        --supporting-evidence "..."

    python responsible_ai.py --seed-demo
        Populates the 5 required Evaluation/Demo examples (idempotent --
        running it twice does not create duplicates).

For automated tests, call add_record(...) directly.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diagnosis  # noqa: E402  (load_case / CaseNotFoundError / check_evidence_grounding)

PROJECT_ROOT = diagnosis.PROJECT_ROOT
RESPONSIBLE_AI_LOG_CSV = os.path.join(PROJECT_ROOT, "responsible_ai_log.csv")

VALID_SOURCE_TYPES = ("Real AI Run", "Evaluation/Demo")
VALID_HUMAN_DECISIONS = ("Edited", "Rejected")

REQUIRED_RECORD_FIELDS = [
    "case_id", "source_type", "ai_root_cause", "human_decision",
    "human_corrected_root_cause", "correction_reason",
    "supporting_evidence", "timestamp",
]

CSV_FIELDNAMES = list(REQUIRED_RECORD_FIELDS)


# ---------------------------------------------------------------------------
# Exceptions -- one per failure mode, each with a clear, human-readable
# message. None of these should surface as a raw Python traceback.
# ---------------------------------------------------------------------------

class ResponsibleAIError(Exception):
    """Base class for all Responsible AI logging errors."""


class InvalidSourceTypeError(ResponsibleAIError):
    """Raised when source_type isn't exactly 'Real AI Run' or 'Evaluation/Demo'."""


class InvalidHumanDecisionError(ResponsibleAIError):
    """Raised when human_decision isn't exactly 'Edited' or 'Rejected'."""


class MissingAIRootCauseError(ResponsibleAIError):
    """Raised when ai_root_cause is empty -- there must be something on
    record to say the human corrected."""


class MissingCorrectionError(ResponsibleAIError):
    """Raised when human_corrected_root_cause is empty."""


class MissingCorrectionReasonError(ResponsibleAIError):
    """Raised when correction_reason is empty."""


class MissingSupportingEvidenceError(ResponsibleAIError):
    """Raised when supporting_evidence is empty."""


class EvidenceNotGroundedError(ResponsibleAIError):
    """Raised when supporting_evidence doesn't actually appear in or
    clearly reference the case's real show-command output -- we never
    log a 'correction' backed by evidence that isn't real."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_record(data: dict, cases_csv: str | None = None) -> dict:
    """Validate one Responsible AI record dict. Confirms the case exists,
    source_type and human_decision are each one of the two allowed
    values, every text field is non-empty, and that supporting_evidence
    is actually grounded in the case's real show-command output (reusing
    Phase 2's evidence-grounding heuristic rather than reinventing it).

    Raises diagnosis.CaseNotFoundError, InvalidSourceTypeError,
    InvalidHumanDecisionError, MissingAIRootCauseError,
    MissingCorrectionError, MissingCorrectionReasonError,
    MissingSupportingEvidenceError, or EvidenceNotGroundedError.

    Returns a normalized copy (whitespace-stripped fields).
    """
    cases_csv = cases_csv or diagnosis.CASES_CSV
    case_id = data.get("case_id")
    case = diagnosis.load_case(case_id, cases_csv)  # raises CaseNotFoundError

    source_type = data.get("source_type")
    if source_type not in VALID_SOURCE_TYPES:
        raise InvalidSourceTypeError(
            f"source_type must be one of {VALID_SOURCE_TYPES}, got {source_type!r}"
        )

    human_decision = data.get("human_decision")
    if human_decision not in VALID_HUMAN_DECISIONS:
        raise InvalidHumanDecisionError(
            f"human_decision must be one of {VALID_HUMAN_DECISIONS}, got {human_decision!r}"
        )

    ai_root_cause = (data.get("ai_root_cause") or "").strip()
    if not ai_root_cause:
        raise MissingAIRootCauseError("ai_root_cause is required and cannot be empty.")

    human_corrected_root_cause = (data.get("human_corrected_root_cause") or "").strip()
    if not human_corrected_root_cause:
        raise MissingCorrectionError("human_corrected_root_cause is required and cannot be empty.")

    correction_reason = (data.get("correction_reason") or "").strip()
    if not correction_reason:
        raise MissingCorrectionReasonError("correction_reason is required and cannot be empty.")

    supporting_evidence = (data.get("supporting_evidence") or "").strip()
    if not supporting_evidence:
        raise MissingSupportingEvidenceError("supporting_evidence is required and cannot be empty.")

    grounded, _, note = diagnosis.check_evidence_grounding(
        [supporting_evidence], case.get("show_output", "")
    )
    if not grounded:
        raise EvidenceNotGroundedError(
            f"supporting_evidence does not appear to be grounded in case "
            f"{case_id!r}'s real show-command output: {note}"
        )

    normalized = dict(data)
    normalized["ai_root_cause"] = ai_root_cause
    normalized["human_corrected_root_cause"] = human_corrected_root_cause
    normalized["correction_reason"] = correction_reason
    normalized["supporting_evidence"] = supporting_evidence
    return normalized


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def save_record(record: dict, out_csv: str = RESPONSIBLE_AI_LOG_CSV) -> None:
    """Append one record to responsible_ai_log.csv, writing the header
    row first if the file doesn't exist yet."""
    file_exists = os.path.exists(out_csv)
    with open(out_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: record.get(k, "") for k in CSV_FIELDNAMES})


def list_records(case_id: str | None = None, csv_path: str = RESPONSIBLE_AI_LOG_CSV) -> list[dict]:
    """Return Responsible AI records, optionally filtered to one
    case_id. Returns an empty list (not an error) if the log doesn't
    exist yet."""
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if case_id:
        rows = [r for r in rows if r.get("case_id") == case_id]
    return rows


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def add_record(
    case_id: str,
    source_type: str,
    ai_root_cause: str,
    human_decision: str,
    human_corrected_root_cause: str,
    correction_reason: str,
    supporting_evidence: str,
    timestamp: str | None = None,
    cases_csv: str | None = None,
    out_csv: str = RESPONSIBLE_AI_LOG_CSV,
    save_csv: bool = True,
) -> dict:
    """Validate and (optionally) persist one Responsible AI record.
    Returns the normalized record with a timestamp filled in if one
    wasn't supplied."""
    raw = {
        "case_id": case_id,
        "source_type": source_type,
        "ai_root_cause": ai_root_cause,
        "human_decision": human_decision,
        "human_corrected_root_cause": human_corrected_root_cause,
        "correction_reason": correction_reason,
        "supporting_evidence": supporting_evidence,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
    }
    normalized = validate_record(raw, cases_csv=cases_csv)
    if save_csv:
        save_record(normalized, out_csv)
    return normalized


# ---------------------------------------------------------------------------
# The 5 required Evaluation/Demo examples.
#
# IMPORTANT: these are hand-written illustrations of realistic AI failure
# patterns, NOT real API output. Every one is clearly marked
# source_type="Evaluation/Demo" and every `supporting_evidence` string is
# a literal excerpt from that case's real `show_output` in cases.csv --
# nothing here is fabricated evidence, only the "ai_root_cause" text
# (a plausible mistake) and the human correction around it are authored
# for demonstration purposes.
#
# Each example illustrates a different failure mode, per the assignment:
#   C002 -> a plausible but incorrect root cause
#   C018 -> missing stronger/more direct evidence
#   C028 -> confusing a Layer 2 problem for a Layer 3/4 one (and vice versa)
#   C024 -> recommending a fix before evidence was sufficient
#   C017 -> identifying only part of a multi-fault case
# ---------------------------------------------------------------------------

def get_demo_examples() -> list[dict]:
    """Return the 5 hand-authored Evaluation/Demo records (unvalidated,
    unsaved). Use seed_demo_examples() to validate and persist them."""
    return [
        {
            "case_id": "C002",
            "source_type": "Evaluation/Demo",
            "ai_root_cause": (
                "VLAN 99 is missing from the VLAN database on SW2, causing "
                "a trunk misconfiguration between SW1 and SW2."
            ),
            "human_decision": "Rejected",
            "human_corrected_root_cause": (
                "Both trunk ends have a native VLAN mismatch (SW1 uses "
                "native VLAN 1, SW2 uses native VLAN 99) -- VLAN 99 isn't "
                "missing, it's simply configured as the native VLAN on one "
                "side only, which causes untagged traffic to leak between "
                "VLANs and triggers a CDP native VLAN mismatch warning."
            ),
            "correction_reason": (
                "The AI pattern-matched this to the more common 'missing "
                "VLAN' failure mode seen in similar cases, but the evidence "
                "here is a CDP native-VLAN-mismatch message, not a missing- "
                "VLAN symptom -- a plausible guess that doesn't fit the "
                "actual evidence."
            ),
            "supporting_evidence": "Native VLAN mismatch discovered on Gi0/1 (1), with SW2 Gi0/1 (99)",
        },
        {
            "case_id": "C018",
            "source_type": "Evaluation/Demo",
            "ai_root_cause": (
                "The internal DNS server is likely down or unreachable, "
                "since name resolution is failing for PC8."
            ),
            "human_decision": "Edited",
            "human_corrected_root_cause": (
                "PC8 is configured with the wrong DNS server address "
                "entirely (203.0.113.9, an external/unreachable address) "
                "instead of the internal lab DNS server -- the DNS server "
                "itself is fine, the client's configuration is wrong."
            ),
            "correction_reason": (
                "The AI jumped to 'the DNS server is down' without noting "
                "the stronger, more direct evidence already in the "
                "ipconfig output: PC8's own DNS Servers line shows an "
                "address (203.0.113.9) that was never the lab's DNS "
                "server. That single line is more conclusive than "
                "speculating about server-side downtime."
            ),
            "supporting_evidence": "DNS Servers......: 203.0.113.9",
        },
        {
            "case_id": "C028",
            "source_type": "Evaluation/Demo",
            "ai_root_cause": (
                "PC1 is likely on the wrong VLAN, since it can reach the "
                "VLAN 30 server via ping but not via HTTP -- suggesting an "
                "inter-VLAN switching issue."
            ),
            "human_decision": "Rejected",
            "human_corrected_root_cause": (
                "This is a Layer 3/4 ACL issue, not a Layer 2 VLAN issue: "
                "the router's SERVER_ACL explicitly denies TCP port 80 "
                "before its final permit-any line, which blocks HTTP while "
                "still allowing ICMP (ping) through -- VLAN assignment is "
                "not involved at all."
            ),
            "correction_reason": (
                "The AI confused a Layer 2 (VLAN/switching) explanation "
                "for what the evidence actually shows is a Layer 3/4 "
                "(ACL/port-filtering) problem. Ping succeeding while HTTP "
                "fails is a classic ACL-port-filtering signature, not a "
                "VLAN symptom -- ICMP and TCP:80 being treated differently "
                "only makes sense at Layer 3/4."
            ),
            "supporting_evidence": "20 deny tcp any any eq 80",
        },
        {
            "case_id": "C024",
            "source_type": "Evaluation/Demo",
            "ai_root_cause": (
                "R1 and R2's OSPF process is misconfigured; recommend "
                "clearing the OSPF process on both routers with "
                "'clear ip ospf process' to force the adjacency to reform."
            ),
            "human_decision": "Edited",
            "human_corrected_root_cause": (
                "R1 and R2 are no longer on the same subnet on their OSPF "
                "link (R1 is 10.0.0.1/30, R2 is 10.0.1.1/30) -- the "
                "adjacency can never form regardless of how many times the "
                "OSPF process is cleared, since OSPF neighbors must share "
                "a subnet. The fix is correcting the IP addressing, not "
                "restarting the process."
            ),
            "correction_reason": (
                "The AI recommended an operational fix (clearing the OSPF "
                "process) before establishing why the neighbor relationship "
                "was actually down. The 'show run interface' output for "
                "both routers was already available and shows the real "
                "cause (mismatched subnets) -- the AI should have checked "
                "that evidence before proposing any fix, since clearing "
                "the process would not have resolved anything."
            ),
            "supporting_evidence": "ip address 10.0.1.1 255.255.255.252",
        },
        {
            "case_id": "C017",
            "source_type": "Evaluation/Demo",
            "ai_root_cause": (
                "The GigabitEthernet0/0.80 subinterface serving VLAN 80 is "
                "down; likely a VLAN 80 configuration problem on that "
                "subinterface."
            ),
            "human_decision": "Edited",
            "human_corrected_root_cause": (
                "GigabitEthernet0/0.80 is only down because its parent "
                "physical interface, GigabitEthernet0/0, is "
                "administratively shut down -- every subinterface on a "
                "shut-down parent goes down with it. The fix is "
                "'no shutdown' on Gi0/0 itself, not any VLAN 80-specific "
                "change on the subinterface."
            ),
            "correction_reason": (
                "The AI correctly identified that the subinterface serving "
                "VLAN 80 is down, but only diagnosed part of the picture -- "
                "it missed that the SAME evidence block also shows the "
                "parent interface Gi0/0 as 'administratively down', which "
                "is the actual root cause. A VLAN 80-specific fix on the "
                "subinterface would not have resolved anything, since the "
                "subinterface can never come up while its parent is shut."
            ),
            "supporting_evidence": "GigabitEthernet0/0     unassigned      YES manual administratively down down",
        },
    ]


def seed_demo_examples(
    out_csv: str = RESPONSIBLE_AI_LOG_CSV, cases_csv: str | None = None
) -> list[dict]:
    """Validate and persist the 5 Evaluation/Demo examples. Idempotent:
    running this twice does not create duplicate rows -- any (case_id,
    source_type="Evaluation/Demo") pair already present is skipped."""
    existing = list_records(csv_path=out_csv)
    already_seeded = {
        r["case_id"] for r in existing if r.get("source_type") == "Evaluation/Demo"
    }

    added = []
    for example in get_demo_examples():
        if example["case_id"] in already_seeded:
            continue
        record = add_record(
            case_id=example["case_id"],
            source_type=example["source_type"],
            ai_root_cause=example["ai_root_cause"],
            human_decision=example["human_decision"],
            human_corrected_root_cause=example["human_corrected_root_cause"],
            correction_reason=example["correction_reason"],
            supporting_evidence=example["supporting_evidence"],
            cases_csv=cases_csv,
            out_csv=out_csv,
        )
        added.append(record)
    return added


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_record(r: dict) -> None:
    print(f"\n[{r['timestamp']}] {r['case_id']} -- {r['source_type']} -- {r['human_decision']}")
    print(f"  AI root cause:        {r['ai_root_cause']}")
    print(f"  Human correction:     {r['human_corrected_root_cause']}")
    print(f"  Correction reason:    {r['correction_reason']}")
    print(f"  Supporting evidence:  {r['supporting_evidence']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="NetSage AI Phase 3C -- Responsible AI log")
    parser.add_argument("--list", action="store_true", help="List Responsible AI records")
    parser.add_argument("--case-id", default=None, help="Filter --list, or the case for --add")
    parser.add_argument("--add", action="store_true", help="Add a record from the flags below")
    parser.add_argument("--seed-demo", action="store_true", help="Populate the 5 required Evaluation/Demo examples")
    parser.add_argument("--source-type", choices=VALID_SOURCE_TYPES, default=None)
    parser.add_argument("--ai-root-cause", default=None)
    parser.add_argument("--human-decision", choices=VALID_HUMAN_DECISIONS, default=None)
    parser.add_argument("--human-corrected-root-cause", default=None)
    parser.add_argument("--correction-reason", default=None)
    parser.add_argument("--supporting-evidence", default=None)
    parser.add_argument("--out", default=RESPONSIBLE_AI_LOG_CSV)
    args = parser.parse_args()

    if args.seed_demo:
        added = seed_demo_examples(out_csv=args.out)
        if not added:
            print("All 5 Evaluation/Demo examples are already present -- nothing to add.")
        else:
            print(f"Added {len(added)} Evaluation/Demo record(s):")
            for r in added:
                _print_record(r)
        return 0

    if args.list:
        rows = list_records(args.case_id, args.out)
        if not rows:
            print("No Responsible AI records found" + (f" for {args.case_id}." if args.case_id else "."))
            return 0
        for r in rows:
            _print_record(r)
        return 0

    if args.add:
        try:
            record = add_record(
                case_id=args.case_id,
                source_type=args.source_type,
                ai_root_cause=args.ai_root_cause,
                human_decision=args.human_decision,
                human_corrected_root_cause=args.human_corrected_root_cause,
                correction_reason=args.correction_reason,
                supporting_evidence=args.supporting_evidence,
                out_csv=args.out,
            )
        except (diagnosis.CaseNotFoundError, InvalidSourceTypeError,
                InvalidHumanDecisionError, MissingAIRootCauseError,
                MissingCorrectionError, MissingCorrectionReasonError,
                MissingSupportingEvidenceError, EvidenceNotGroundedError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print("Saved Responsible AI record:")
        _print_record(record)
        print(f"\nWritten to {args.out}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
