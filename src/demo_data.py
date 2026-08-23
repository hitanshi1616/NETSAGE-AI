#!/usr/bin/env python3
"""
demo_data.py
-------------
NetSage AI — Offline demo data generator (no Anthropic API required).

This project can't always assume an ANTHROPIC_API_KEY is configured (e.g.
no API credits purchased yet). This module lets you demonstrate the
COMPLETE pipeline -- AI diagnosis -> human review -> verification ->
dashboard -- entirely offline, using hand-authored diagnosis records
that reuse the 38 real cases' actual show-command evidence.

CRITICAL DISTINCTION: these are NOT real Anthropic API responses. Every
record generated here is written with source_type = "Evaluation/Demo" in
ai_diagnoses.csv (the same column and vocabulary Phase 2 already added
for exactly this purpose -- see diagnosis.SOURCE_TYPE_EVALUATION_DEMO).
A real run of `python diagnosis.py --case-id X` always writes
source_type = "Real AI Run". This module never claims otherwise, and
never calls the network in any way.

Every record is validated through the SAME functions a real API response
would go through -- diagnosis.validate_diagnosis_schema() and
diagnosis.check_evidence_grounding() -- so a demo record that claimed
evidence not actually in the case would be caught exactly like a real
ungrounded AI response would be.

CLI usage:
    python demo_data.py --seed
        Generate and save the 5 demo diagnoses to ai_diagnoses.csv
        (idempotent -- running it twice does not create duplicates).

    python demo_data.py --list
        List demo (Evaluation/Demo) records currently in ai_diagnoses.csv.

After seeding, the existing tools work unmodified:
    python review.py --case-id C001       # Accept/Edit/Reject
    python verify.py --case-id C001 --after-evidence "..."
    streamlit run ../app/dashboard.py     # picks it up automatically
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diagnosis  # noqa: E402

CASES_CSV = diagnosis.CASES_CSV
AI_DIAGNOSES_CSV = diagnosis.AI_DIAGNOSES_CSV
DEMO_MODEL_LABEL = "offline-demo-data (no API call)"


# ---------------------------------------------------------------------------
# The 5 hand-authored demo diagnoses.
#
# Every `observed_evidence` string below is a paraphrase of a literal fact
# from that case's real `show_output` in cases.csv -- nothing here is
# invented Cisco output. Each is designed to demonstrate a different point
# in the human-review workflow:
#
#   C001 -> correct diagnosis (missing VLAN)              -> Accept-worthy
#   C022 -> correct diagnosis, different evidence (routing) -> Accept-worthy
#   C009 -> correct diagnosis (duplicate IP)                -> Accept-worthy
#   C008 -> plausible but imprecise (wrong subnet mask)      -> Edit-worthy
#   C028 -> plausible but WRONG (ACL mistaken for VLAN)      -> Reject-worthy
# ---------------------------------------------------------------------------

def get_demo_diagnoses() -> list[dict]:
    """Return the 5 hand-authored demo diagnosis records (unvalidated,
    unsaved, no timestamp yet). Use seed_demo_diagnoses() to validate,
    timestamp, and persist them."""
    return [
        {
            "case_id": "C001",
            "root_cause": (
                "VLAN 10 is assigned to Fa0/5 but does not exist in the VLAN "
                "database, leaving the port inactive."
            ),
            "confidence": 92,
            "osi_layer": "Layer 2",
            "observed_evidence": [
                "show interfaces fa0/5 switchport shows Access Mode VLAN: 10 (Inactive)",
                "show vlan brief lists only VLAN 1 and 20 -- VLAN 10 is absent from the database",
            ],
            "inference": (
                "An access port assigned to a VLAN that isn't in the VLAN "
                "database is placed in an inactive state, which explains why "
                "the port has no working Layer 2 path at all."
            ),
            "next_command": "show vlan brief",
            "fix_steps": [
                "Enter global configuration mode on SW1",
                "Create the missing VLAN: vlan 10",
                "Name it: name SALES",
                "Verify the port is now active: show interfaces fa0/5 switchport",
            ],
            "human_review_required": True,
        },
        {
            "case_id": "C022",
            "root_cause": (
                "The router has no route or subinterface for the "
                "192.168.30.0/24 (VLAN 30) subnet, so traffic past the "
                "gateway is dropped."
            ),
            "confidence": 78,
            "osi_layer": "Layer 3",
            "observed_evidence": [
                "show ip route lists 192.168.10.0/24 and 192.168.20.0/24 as the only directly connected routes",
                "show access-lists shows no access-lists configured, ruling out an ACL block",
            ],
            "inference": (
                "Gateway reachability plus a total timeout to the VLAN 30 "
                "host, combined with no ACLs configured and no route to that "
                "subnet, points to a routing gap rather than a security block."
            ),
            "next_command": "show ip interface brief (check whether a Gi0/0.30 subinterface exists and is up/up)",
            "fix_steps": [
                "Verify VLAN 30 trunking exists to the router",
                "Create the missing subinterface: interface Gi0/0.30, encapsulation dot1Q 30, ip address 192.168.30.1 255.255.255.0",
                "Re-test: ping 192.168.30.50 from the PC",
            ],
            "human_review_required": True,
        },
        {
            "case_id": "C009",
            "root_cause": (
                "PC5 and PC6 are both statically configured with "
                "192.168.10.20, causing an IP address conflict on VLAN 10."
            ),
            "confidence": 95,
            "osi_layer": "Layer 3",
            "observed_evidence": [
                "PC5's ipconfig shows IP Address......: 192.168.10.20",
                "PC6's ipconfig also shows IP Address......: 192.168.10.20",
                "The %DUPADDR-3-DUPLICATE_ADDRESS message confirms a duplicate address conflict on VLAN10",
            ],
            "inference": (
                "Two hosts on the same VLAN reporting the identical static IP, "
                "combined with a native duplicate-address log message, is "
                "conclusive evidence of an address conflict rather than any "
                "ambiguity requiring further investigation."
            ),
            "next_command": "",
            "fix_steps": [
                "Reassign PC6 to an unused address such as 192.168.10.21",
                "Verify with ipconfig on both hosts",
            ],
            "human_review_required": True,
        },
        {
            "case_id": "C008",
            "root_cause": (
                "PC4 has an incorrect subnet mask configuration causing it to "
                "miscalculate its local subnet boundaries, isolating it from "
                "other hosts on VLAN 20."
            ),
            "confidence": 68,
            "osi_layer": "Layer 3",
            "observed_evidence": [
                "PC4's ipconfig shows Subnet Mask......: 255.255.255.192",
                "show ip interface brief shows GigabitEthernet0/0.20 with IP 192.168.20.1",
            ],
            "inference": (
                "A /26 mask on a host where the VLAN's router interface is /24 "
                "causes the host to compute a smaller subnet than the network "
                "actually uses, treating some legitimate local peers as remote."
            ),
            "next_command": "show ip interface brief (confirm the VLAN 20 subinterface's actual mask)",
            "fix_steps": [
                "Adjust PC4's subnet mask to match the VLAN 20 configuration",
            ],
            "human_review_required": True,
            # NOTE: this diagnosis is deliberately imprecise -- it never
            # states the specific corrected mask (255.255.255.0), unlike the
            # real case's ground truth. This is intentional: it's designed
            # to demonstrate an "Edited" review, where a human tightens a
            # correct-but-vague AI answer into a precise, actionable one.
        },
        {
            "case_id": "C028",
            "root_cause": (
                "PC1 is likely on the wrong VLAN, since it can reach the "
                "VLAN 30 server via ping but not via HTTP, suggesting an "
                "inter-VLAN switching issue rather than a filtering rule."
            ),
            "confidence": 55,
            "osi_layer": "Layer 2",
            "observed_evidence": [
                "show access-lists shows an access list named SERVER_ACL configured on the router",
                "show run interface gi0/0.30 shows ip access-group SERVER_ACL applied inbound",
            ],
            "inference": (
                "Since ICMP reaches the server but the higher-layer "
                "application traffic does not, the AI attributes this to a "
                "Layer 2 VLAN misconfiguration rather than examining the "
                "specific ACL rule contents that were already in evidence."
            ),
            "next_command": "show access-lists",
            "fix_steps": [
                "Review VLAN assignment for PC1's port",
            ],
            "human_review_required": True,
            # NOTE: this diagnosis is WRONG. The real cause (visible in the
            # same show_output cited above) is that SERVER_ACL explicitly
            # denies "tcp any any eq 80" before its final permit-any line --
            # a Layer 3/4 ACL problem, not a Layer 2 VLAN problem. This is
            # intentional: it demonstrates a "Rejected" review, where a
            # human catches a plausible-sounding but incorrect AI diagnosis.
        },
    ]


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def _already_seeded_case_ids(out_csv: str) -> set[str]:
    if not os.path.exists(out_csv):
        return set()
    rows = diagnosis_load_ai_rows(out_csv)
    return {
        r.get("case_id") for r in rows
        if r.get("source_type") == diagnosis.SOURCE_TYPE_EVALUATION_DEMO
    }


def diagnosis_load_ai_rows(csv_path: str) -> list[dict]:
    """Small local helper so this module doesn't need to duplicate CSV
    parsing -- reuses the csv module the same way every other Phase 3
    module does."""
    import csv
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_demo_result(raw: dict, cases_csv: str = CASES_CSV) -> dict:
    """Validate one hand-authored demo diagnosis (schema + evidence
    grounding, reusing Phase 2's exact validation functions) and build
    the full result dict in the same shape diagnose_case() returns --
    so it can be saved with the SAME save_diagnosis_to_csv() function
    real API results use, and so a demo record that claimed unsupported
    evidence would be caught exactly like a real one would be.

    Raises diagnosis.CaseNotFoundError, diagnosis.ValidationError, or
    a ValueError (if observed_evidence isn't actually grounded in the
    case's real show_output) rather than silently accepting bad data.
    """
    case = diagnosis.load_case(raw["case_id"], cases_csv)

    # Reuse the exact schema validator a real API response goes through.
    normalized = diagnosis.validate_diagnosis_schema({
        "root_cause": raw["root_cause"],
        "confidence": raw["confidence"],
        "osi_layer": raw["osi_layer"],
        "observed_evidence": raw["observed_evidence"],
        "inference": raw["inference"],
        "next_command": raw["next_command"],
        "fix_steps": raw["fix_steps"],
        "human_review_required": raw["human_review_required"],
    })

    # Reuse the exact evidence-grounding check a real API response goes
    # through -- this is what guarantees every claimed observed-evidence
    # item is actually supported by the real case evidence, not invented.
    grounded, item_results, grounding_note = diagnosis.check_evidence_grounding(
        normalized["observed_evidence"], case.get("show_output", "")
    )
    if not grounded:
        ungrounded = [r["evidence"] for r in item_results if not r["grounded"]]
        raise ValueError(
            f"Demo diagnosis for {raw['case_id']!r} contains observed_evidence "
            f"that isn't grounded in the case's real show_output: {ungrounded}. "
            f"Refusing to seed unsupported demo evidence."
        )

    findings = diagnosis.get_checker_findings(case)

    return {
        "case_id": raw["case_id"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": DEMO_MODEL_LABEL,
        "root_cause": normalized["root_cause"],
        "confidence": normalized["confidence"],
        "osi_layer": normalized["osi_layer"],
        "observed_evidence": normalized["observed_evidence"],
        "inference": normalized["inference"],
        "next_command": normalized["next_command"],
        "fix_steps": normalized["fix_steps"],
        "human_review_required": normalized["human_review_required"],
        "evidence_grounded": grounded,
        "ungrounded_evidence_items": [],
        "grounding_note": grounding_note,
        "checker_findings_count": len(findings),
        "source_type": diagnosis.SOURCE_TYPE_EVALUATION_DEMO,
    }


def seed_demo_diagnoses(
    out_csv: str = AI_DIAGNOSES_CSV, cases_csv: str = CASES_CSV
) -> list[dict]:
    """Validate and persist the 5 demo diagnoses to ai_diagnoses.csv.
    Idempotent: any case_id that already has an Evaluation/Demo row in
    out_csv is skipped, so running this twice never creates duplicates.
    Never calls the network -- every record is built from the hand-
    authored data in get_demo_diagnoses() above."""
    already_seeded = _already_seeded_case_ids(out_csv)

    added = []
    for raw in get_demo_diagnoses():
        if raw["case_id"] in already_seeded:
            continue
        result = build_demo_result(raw, cases_csv=cases_csv)
        diagnosis.save_diagnosis_to_csv(result, out_csv)
        added.append(result)
    return added


def list_demo_diagnoses(csv_path: str = AI_DIAGNOSES_CSV) -> list[dict]:
    """Return only the Evaluation/Demo rows currently in ai_diagnoses.csv
    (i.e. not real API results). Empty list if the file doesn't exist."""
    if not os.path.exists(csv_path):
        return []
    rows = diagnosis_load_ai_rows(csv_path)
    return [r for r in rows if r.get("source_type") == diagnosis.SOURCE_TYPE_EVALUATION_DEMO]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_record(r: dict) -> None:
    print(f"\n[{r.get('timestamp', '')}] {r['case_id']} -- {r.get('source_type', '')}")
    print(f"  Root cause:  {r['root_cause']}")
    print(f"  Confidence:  {r['confidence']}/100")
    print(f"  OSI layer:   {r['osi_layer']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NetSage AI offline demo-data generator (no Anthropic API calls)"
    )
    parser.add_argument("--seed", action="store_true", help="Generate and save the 5 demo diagnoses")
    parser.add_argument("--list", action="store_true", help="List Evaluation/Demo records in ai_diagnoses.csv")
    parser.add_argument("--out", default=AI_DIAGNOSES_CSV)
    args = parser.parse_args()

    if args.seed:
        added = seed_demo_diagnoses(out_csv=args.out)
        if not added:
            print("All 5 demo diagnoses are already present -- nothing to add.")
        else:
            print(f"Added {len(added)} Evaluation/Demo diagnosis record(s) to {args.out}")
            print("(No Anthropic API was called -- these are hand-authored offline demo records.)")
            for r in added:
                _print_record(r)
        return 0

    if args.list:
        records = list_demo_diagnoses(args.out)
        if not records:
            print("No Evaluation/Demo diagnosis records found.")
            return 0
        for r in records:
            _print_record(r)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
