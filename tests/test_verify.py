"""
test_verify.py
---------------
Phase 3B tests for src/verify.py: verification of all six deterministic
fault types (Resolved / Not Resolved / Inconclusive), missing after
evidence, invalid case ID, invalid verification status, persistence, and
the core safety property that a case can never be marked Resolved
without supporting after-evidence.

Run from the project root:
    python3 -m pytest tests/test_verify.py -v
"""

import csv
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import verify  # noqa: E402
import diagnosis  # noqa: E402
import review  # noqa: E402


@pytest.fixture
def review_csv_with_accepted(tmp_path):
    """A review log with one Accepted review for C007, so verify_case can
    resolve a final_diagnosis without needing an explicit override."""
    path = tmp_path / "review_log.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=review.CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerow({
            "case_id": "C007",
            "ai_root_cause": "PC2's default gateway does not match VLAN 10's router interface.",
            "ai_confidence": "90",
            "review_status": "Accepted",
            "final_root_cause": "PC2's default gateway does not match VLAN 10's router interface.",
            "reviewer_notes": "",
            "evidence_used": "[]",
            "timestamp": "2026-01-01T00:00:00+00:00",
        })
    return str(path)


def _review_csv_for(tmp_path, case_id, status, final_root_cause):
    path = tmp_path / f"review_log_{case_id}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=review.CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerow({
            "case_id": case_id, "ai_root_cause": "ai cause", "ai_confidence": "80",
            "review_status": status, "final_root_cause": final_root_cause,
            "reviewer_notes": "note", "evidence_used": "[]",
            "timestamp": "2026-01-01T00:00:00+00:00",
        })
    return str(path)


@pytest.fixture
def verification_csv(tmp_path):
    return str(tmp_path / "verification_log.csv")


# ---------------------------------------------------------------------------
# 1 & 2. Missing-route verification: successful and failed
# ---------------------------------------------------------------------------

def test_missing_route_resolved(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C022", "Accepted", "Missing route to VLAN 30")
    after = """
Router#show ip route
     192.168.10.0/24 is directly connected, GigabitEthernet0/0.10
     192.168.20.0/24 is directly connected, GigabitEthernet0/0.20
     192.168.30.0/24 is directly connected, GigabitEthernet0/0.30
"""
    result = verify.verify_case(
        "C022", after_evidence=after, review_csv=review_csv,
        out_csv=verification_csv,
    )
    assert result["verification_status"] == "Resolved"
    assert "192.168.30.0" in result["verification_reason"]


def test_missing_route_not_resolved(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C022", "Accepted", "Missing route to VLAN 30")
    after = """
Router#show ip route
     192.168.10.0/24 is directly connected, GigabitEthernet0/0.10
     192.168.20.0/24 is directly connected, GigabitEthernet0/0.20
"""
    result = verify.verify_case(
        "C022", after_evidence=after, review_csv=review_csv,
        out_csv=verification_csv,
    )
    assert result["verification_status"] == "Not Resolved"


# ---------------------------------------------------------------------------
# 3. Inconclusive verification
# ---------------------------------------------------------------------------

def test_inconclusive_when_after_evidence_unrelated(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C022", "Accepted", "Missing route to VLAN 30")
    after = "PC1> ping 8.8.8.8\nRequest timed out.\n"  # no route/IP-table content at all
    result = verify.verify_case(
        "C022", after_evidence=after, review_csv=review_csv,
        out_csv=verification_csv,
    )
    assert result["verification_status"] == "Inconclusive"


def test_inconclusive_when_no_original_deterministic_finding(tmp_path, verification_csv):
    """C028 is an ACL case -- no deterministic checker finding exists for
    it, so verification must not pretend to know whether it's fixed."""
    review_csv = _review_csv_for(tmp_path, "C028", "Accepted", "ACL blocks HTTP")
    result = verify.verify_case(
        "C028", after_evidence="Router#show access-lists\n(no access-lists configured)",
        review_csv=review_csv, out_csv=verification_csv,
    )
    assert result["verification_status"] == "Inconclusive"
    assert "isn't available for this fault type" in result["verification_reason"]


# ---------------------------------------------------------------------------
# 4. Interface-down -> interface-up verification
# ---------------------------------------------------------------------------

def test_interface_down_resolved(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C027", "Edited", "Interface administratively down")
    after = """
Router#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/1     unassigned      YES manual up                    up
GigabitEthernet0/1.100 192.168.100.1   YES manual up                    up
"""
    result = verify.verify_case(
        "C027", after_evidence=after, review_csv=review_csv,
        out_csv=verification_csv,
    )
    assert result["verification_status"] == "Resolved"


def test_interface_down_not_resolved(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C027", "Edited", "Interface administratively down")
    after = """
Router#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/1     unassigned      YES manual administratively down down
GigabitEthernet0/1.100 192.168.100.1   YES manual up                    down
"""
    result = verify.verify_case(
        "C027", after_evidence=after, review_csv=review_csv,
        out_csv=verification_csv,
    )
    assert result["verification_status"] == "Not Resolved"


# ---------------------------------------------------------------------------
# 5. Gateway mismatch -> corrected gateway
# ---------------------------------------------------------------------------

def test_gateway_mismatch_resolved(tmp_path, verification_csv, review_csv_with_accepted):
    after = "PC2> ipconfig\nIP Address......: 192.168.10.55\nDefault Gateway...: 192.168.10.1\n"
    result = verify.verify_case(
        "C007", after_evidence=after, review_csv=review_csv_with_accepted,
        out_csv=verification_csv,
    )
    assert result["verification_status"] == "Resolved"


def test_gateway_mismatch_not_resolved(tmp_path, verification_csv, review_csv_with_accepted):
    after = "PC2> ipconfig\nIP Address......: 192.168.10.55\nDefault Gateway...: 192.168.1.1\n"
    result = verify.verify_case(
        "C007", after_evidence=after, review_csv=review_csv_with_accepted,
        out_csv=verification_csv,
    )
    assert result["verification_status"] == "Not Resolved"


# ---------------------------------------------------------------------------
# 6. Missing VLAN -> VLAN present
# ---------------------------------------------------------------------------

def test_missing_vlan_resolved(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C001", "Accepted", "Missing VLAN 10")
    after = """
SW1#show vlan brief
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active    Fa0/1, Fa0/2, Fa0/3, Fa0/4
10   SALES                            active    Fa0/5
20   ENGINEERING                      active    Fa0/6, Fa0/7
"""
    result = verify.verify_case(
        "C001", after_evidence=after, review_csv=review_csv,
        out_csv=verification_csv,
    )
    assert result["verification_status"] == "Resolved"


def test_missing_vlan_not_resolved(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C001", "Accepted", "Missing VLAN 10")
    after = """
SW1#show vlan brief
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active    Fa0/1, Fa0/2, Fa0/3, Fa0/4
20   ENGINEERING                      active    Fa0/6, Fa0/7
"""
    result = verify.verify_case(
        "C001", after_evidence=after, review_csv=review_csv,
        out_csv=verification_csv,
    )
    assert result["verification_status"] == "Not Resolved"


def test_missing_vlan_still_inactive_is_not_resolved(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C001", "Accepted", "Missing VLAN 10")
    after = "SW1#show interfaces fa0/5 switchport\nAccess Mode VLAN: 10 (Inactive)\n"
    result = verify.verify_case(
        "C001", after_evidence=after, review_csv=review_csv,
        out_csv=verification_csv,
    )
    assert result["verification_status"] == "Not Resolved"


# ---------------------------------------------------------------------------
# 7. Duplicate IP -> duplicate removed
# ---------------------------------------------------------------------------

def test_duplicate_ip_resolved(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C009", "Accepted", "Duplicate IP 192.168.10.20")
    after = "PC6> ipconfig\nIP Address......: 192.168.10.21\nSubnet Mask......: 255.255.255.0\n"
    result = verify.verify_case(
        "C009", after_evidence=after, review_csv=review_csv,
        out_csv=verification_csv,
    )
    assert result["verification_status"] == "Resolved"


def test_duplicate_ip_not_resolved(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C009", "Accepted", "Duplicate IP 192.168.10.20")
    after = (
        "PC5> ipconfig\nIP Address......: 192.168.10.20\n\n"
        "PC6> ipconfig\nIP Address......: 192.168.10.20\n"
    )
    result = verify.verify_case(
        "C009", after_evidence=after, review_csv=review_csv,
        out_csv=verification_csv,
    )
    assert result["verification_status"] == "Not Resolved"


def test_duplicate_ip_inconclusive_when_ip_not_mentioned(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C009", "Accepted", "Duplicate IP 192.168.10.20")
    after = "PC5> ping 192.168.10.1\nReply from 192.168.10.1\n"
    result = verify.verify_case(
        "C009", after_evidence=after, review_csv=review_csv,
        out_csv=verification_csv,
    )
    assert result["verification_status"] == "Inconclusive"


# ---------------------------------------------------------------------------
# 8. Wrong mask -> corrected mask
# ---------------------------------------------------------------------------

def test_wrong_subnet_mask_resolved(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C008", "Accepted", "Wrong subnet mask on PC4")
    after = "PC4> ipconfig\nIP Address......: 192.168.20.40\nSubnet Mask......: 255.255.255.0\n"
    result = verify.verify_case(
        "C008", after_evidence=after, review_csv=review_csv,
        out_csv=verification_csv,
    )
    assert result["verification_status"] == "Resolved"


def test_wrong_subnet_mask_not_resolved(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C008", "Accepted", "Wrong subnet mask on PC4")
    after = "PC4> ipconfig\nIP Address......: 192.168.20.40\nSubnet Mask......: 255.255.255.192\n"
    result = verify.verify_case(
        "C008", after_evidence=after, review_csv=review_csv,
        out_csv=verification_csv,
    )
    assert result["verification_status"] == "Not Resolved"


# ---------------------------------------------------------------------------
# Multi-finding case (both must resolve for overall Resolved)
# ---------------------------------------------------------------------------

def test_multi_finding_case_all_resolved(tmp_path, verification_csv):
    """C025 originally has BOTH interface_down and missing_route findings.
    Overall status should only be Resolved if both are fixed."""
    review_csv = _review_csv_for(tmp_path, "C025", "Accepted", "New VLAN 90 subinterface down/misrouted")
    after = """
Router#show ip interface brief
GigabitEthernet0/0.90  192.168.90.1    YES manual up                    up

Router#show ip route
     192.168.10.0/24 is directly connected, GigabitEthernet0/0.10
     192.168.20.0/24 is directly connected, GigabitEthernet0/0.20
     192.168.90.0/24 is directly connected, GigabitEthernet0/0.90
"""
    result = verify.verify_case(
        "C025", after_evidence=after, review_csv=review_csv, out_csv=verification_csv,
    )
    assert result["verification_status"] == "Resolved"
    assert {"interface_down", "missing_route"}.issubset(set(result["checks_evaluated"]))


def test_multi_finding_case_partial_fix_is_not_resolved(tmp_path, verification_csv):
    """Only the interface came up; the route is still missing -> overall
    must be Not Resolved, not Resolved."""
    review_csv = _review_csv_for(tmp_path, "C025", "Accepted", "New VLAN 90 subinterface down/misrouted")
    after = """
Router#show ip interface brief
GigabitEthernet0/0.90  192.168.90.1    YES manual up                    up

Router#show ip route
     192.168.10.0/24 is directly connected, GigabitEthernet0/0.10
     192.168.20.0/24 is directly connected, GigabitEthernet0/0.20
"""
    result = verify.verify_case(
        "C025", after_evidence=after, review_csv=review_csv, out_csv=verification_csv,
    )
    assert result["verification_status"] == "Not Resolved"


# ---------------------------------------------------------------------------
# 9. Missing after-evidence
# ---------------------------------------------------------------------------

def test_missing_after_evidence_raises(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C022", "Accepted", "Missing route")
    with pytest.raises(verify.MissingAfterEvidenceError):
        verify.verify_case("C022", after_evidence="", review_csv=review_csv, out_csv=verification_csv)


def test_missing_after_evidence_whitespace_only_raises(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C022", "Accepted", "Missing route")
    with pytest.raises(verify.MissingAfterEvidenceError):
        verify.verify_case("C022", after_evidence="   \n  ", review_csv=review_csv, out_csv=verification_csv)


def test_missing_after_evidence_none_raises(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C022", "Accepted", "Missing route")
    with pytest.raises(verify.MissingAfterEvidenceError):
        verify.verify_case("C022", after_evidence=None, review_csv=review_csv, out_csv=verification_csv)


# ---------------------------------------------------------------------------
# 10. Invalid case ID
# ---------------------------------------------------------------------------

def test_invalid_case_id_raises(verification_csv):
    with pytest.raises(diagnosis.CaseNotFoundError):
        verify.verify_case("C999", after_evidence="some evidence", out_csv=verification_csv)


# ---------------------------------------------------------------------------
# 11. Invalid verification status
# ---------------------------------------------------------------------------

def test_validate_verification_status_rejects_bad_value():
    with pytest.raises(verify.InvalidVerificationStatusError):
        verify.validate_verification_status("Fixed")


def test_validate_verification_status_accepts_all_three_valid_values():
    for status in verify.VALID_STATUSES:
        verify.validate_verification_status(status)  # should not raise


def test_save_verification_record_rejects_invalid_status(verification_csv):
    bad_record = {
        "case_id": "C001", "final_diagnosis": "x", "verification_status": "Kinda Fixed",
        "before_evidence": "x", "after_evidence": "y", "verification_reason": "z",
        "timestamp": "2026-01-01T00:00:00+00:00", "checks_evaluated": [],
    }
    with pytest.raises(verify.InvalidVerificationStatusError):
        verify.save_verification_record(bad_record, out_csv=verification_csv)


# ---------------------------------------------------------------------------
# 12. Persistent verification record
# ---------------------------------------------------------------------------

def test_verification_persists_all_required_fields(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C022", "Accepted", "Missing route to VLAN 30")
    after = "Router#show ip route\n192.168.30.0/24 is directly connected, GigabitEthernet0/0.30\n"
    verify.verify_case("C022", after_evidence=after, review_csv=review_csv, out_csv=verification_csv)

    assert os.path.exists(verification_csv)
    with open(verification_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    for field in verify.REQUIRED_RECORD_FIELDS:
        assert field in rows[0]
    assert rows[0]["case_id"] == "C022"
    assert rows[0]["verification_status"] == "Resolved"


def test_multiple_verifications_append_rather_than_overwrite(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C022", "Accepted", "Missing route")
    verify.verify_case("C022", after_evidence="no route here", review_csv=review_csv, out_csv=verification_csv)
    verify.verify_case(
        "C022", after_evidence="192.168.30.0/24 is directly connected",
        review_csv=review_csv, out_csv=verification_csv,
    )
    with open(verification_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2


def test_list_verifications_filters_by_case_id(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C022", "Accepted", "Missing route")
    verify.verify_case(
        "C022", after_evidence="192.168.30.0/24 is directly connected",
        review_csv=review_csv, out_csv=verification_csv,
    )
    rows = verify.list_verifications("C022", csv_path=verification_csv)
    assert len(rows) == 1
    assert rows[0]["case_id"] == "C022"


def test_list_verifications_empty_when_no_log_exists(tmp_path):
    nonexistent = str(tmp_path / "does_not_exist.csv")
    assert verify.list_verifications("C022", csv_path=nonexistent) == []


# ---------------------------------------------------------------------------
# 13. Ensure no verification can be marked Resolved without supporting
#     after-evidence (the core safety property of this whole module)
# ---------------------------------------------------------------------------

def test_cannot_be_resolved_without_after_evidence_at_all(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C022", "Accepted", "Missing route")
    with pytest.raises(verify.MissingAfterEvidenceError):
        verify.verify_case("C022", after_evidence="", review_csv=review_csv, out_csv=verification_csv)
    # And confirm nothing was written to the CSV as a side effect.
    assert not os.path.exists(verification_csv)


def test_resolution_requires_the_specific_fact_not_just_any_text(tmp_path, verification_csv):
    """Pasting unrelated (but non-empty) after-evidence must not be
    treated as Resolved just because *some* evidence was supplied."""
    review_csv = _review_csv_for(tmp_path, "C022", "Accepted", "Missing route")
    after = "This is just some unrelated text about the weather in the lab room."
    result = verify.verify_case(
        "C022", after_evidence=after, review_csv=review_csv, out_csv=verification_csv,
    )
    assert result["verification_status"] != "Resolved"


def test_missing_final_diagnosis_without_review_or_override_raises(verification_csv):
    """No review on file and no explicit final_diagnosis override ->
    verification must refuse rather than guessing at a diagnosis."""
    with pytest.raises(verify.MissingFinalDiagnosisError):
        verify.verify_case(
            "C022", after_evidence="192.168.30.0/24 is directly connected",
            review_csv=str(__import__("pathlib").Path(verification_csv).parent / "no_reviews.csv"),
            out_csv=verification_csv,
        )


def test_explicit_final_diagnosis_override_bypasses_review_requirement(verification_csv, tmp_path):
    """An explicit final_diagnosis lets verification proceed even with no
    review on file -- useful for direct/manual demoing."""
    no_reviews_csv = str(tmp_path / "no_reviews.csv")
    result = verify.verify_case(
        "C022", after_evidence="192.168.30.0/24 is directly connected",
        final_diagnosis="Missing route (manually confirmed)",
        review_csv=no_reviews_csv, out_csv=verification_csv,
    )
    assert result["final_diagnosis"] == "Missing route (manually confirmed)"


def test_malformed_after_evidence_raises(verification_csv):
    with pytest.raises(verify.MalformedEvidenceError):
        verify.verify_case(
            "C022", after_evidence=["not", "a", "string"],
            final_diagnosis="Missing route", out_csv=verification_csv,
        )


def test_malformed_before_evidence_raises(verification_csv):
    with pytest.raises(verify.MalformedEvidenceError):
        verify.verify_case(
            "C022", after_evidence="192.168.30.0/24 is directly connected",
            before_evidence=12345,
            final_diagnosis="Missing route", out_csv=verification_csv,
        )


def test_before_evidence_defaults_to_case_show_output(tmp_path, verification_csv):
    review_csv = _review_csv_for(tmp_path, "C022", "Accepted", "Missing route")
    result = verify.verify_case(
        "C022", after_evidence="192.168.30.0/24 is directly connected",
        review_csv=review_csv, out_csv=verification_csv,
    )
    case = diagnosis.load_case("C022")
    assert result["before_evidence"] == case["show_output"]


# ---------------------------------------------------------------------------
# Safety: verifier never touches network/device configuration
# ---------------------------------------------------------------------------

def test_verify_module_has_no_device_automation_capability():
    forbidden_terms = ["paramiko", "netmiko", "telnetlib", "subprocess",
                        "os.system", "napalm", "ncclient"]
    src_path = os.path.join(os.path.dirname(__file__), "..", "src", "verify.py")
    with open(src_path, encoding="utf-8") as f:
        source = f.read()
    for term in forbidden_terms:
        assert term not in source, f"verify.py unexpectedly references {term!r}"
