"""
test_checker.py
----------------
Unit tests for each of the six deterministic checks (positive + negative
cases with hand-built snapshots), plus an integration test that runs the
checker against the full cases.csv and verifies it agrees with the
ground-truth `checker_findable` / `checker_check` columns baked into the
dataset by generate_cases.py.

Run from the project root:
    pytest -v
or:
    python3 -m pytest tests/ -v
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "checker"))

from checker import (  # noqa: E402
    check_duplicate_ip,
    check_wrong_subnet_mask,
    check_gateway_mismatch,
    check_interface_down,
    check_missing_vlan,
    check_missing_route,
    run_all_checks,
    run_checks_on_cases_csv,
)

CASES_CSV = os.path.join(os.path.dirname(__file__), "..", "cases", "cases.csv")


# ---------------------------------------------------------------------------
# 1. duplicate_ip
# ---------------------------------------------------------------------------

def test_duplicate_ip_detected():
    snapshot = {
        "hosts": [
            {"name": "PC1", "ip": "192.168.10.10", "mask": "255.255.255.0", "gateway": "192.168.10.1", "vlan": 10},
            {"name": "PC2", "ip": "192.168.10.10", "mask": "255.255.255.0", "gateway": "192.168.10.1", "vlan": 10},
        ],
    }
    findings = check_duplicate_ip(snapshot)
    assert len(findings) == 1
    assert findings[0].check == "duplicate_ip"
    assert "PC1" in findings[0].details["owners"] and "PC2" in findings[0].details["owners"]


def test_duplicate_ip_not_flagged_when_unique():
    snapshot = {
        "hosts": [
            {"name": "PC1", "ip": "192.168.10.10", "mask": "255.255.255.0", "gateway": "192.168.10.1", "vlan": 10},
            {"name": "PC2", "ip": "192.168.10.11", "mask": "255.255.255.0", "gateway": "192.168.10.1", "vlan": 10},
        ],
    }
    assert check_duplicate_ip(snapshot) == []


def test_duplicate_ip_ignores_missing_ips():
    snapshot = {"hosts": [{"name": "PC1", "ip": None, "vlan": 10}, {"name": "PC2", "ip": None, "vlan": 10}]}
    assert check_duplicate_ip(snapshot) == []


# ---------------------------------------------------------------------------
# 2. wrong_subnet_mask
# ---------------------------------------------------------------------------

def test_wrong_subnet_mask_detected():
    snapshot = {
        "hosts": [{"name": "PC1", "ip": "192.168.10.10", "mask": "255.255.255.128", "gateway": "192.168.10.1", "vlan": 10}],
        "interfaces": [{"name": "Gi0/0.10", "ip": "192.168.10.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 10}],
    }
    findings = check_wrong_subnet_mask(snapshot)
    assert len(findings) == 1
    assert findings[0].details["host_mask"] == "255.255.255.128"
    assert findings[0].details["expected_mask"] == "255.255.255.0"


def test_wrong_subnet_mask_matching_is_clean():
    snapshot = {
        "hosts": [{"name": "PC1", "ip": "192.168.10.10", "mask": "255.255.255.0", "gateway": "192.168.10.1", "vlan": 10}],
        "interfaces": [{"name": "Gi0/0.10", "ip": "192.168.10.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 10}],
    }
    assert check_wrong_subnet_mask(snapshot) == []


def test_wrong_subnet_mask_no_reference_interface_is_silent():
    # If we don't know the "true" mask for the VLAN, we shouldn't guess.
    snapshot = {"hosts": [{"name": "PC1", "ip": "192.168.10.10", "mask": "255.255.255.128", "gateway": "192.168.10.1", "vlan": 10}]}
    assert check_wrong_subnet_mask(snapshot) == []


# ---------------------------------------------------------------------------
# 3. gateway_mismatch
# ---------------------------------------------------------------------------

def test_gateway_mismatch_detected_against_known_interface():
    snapshot = {
        "hosts": [{"name": "PC1", "ip": "192.168.10.10", "mask": "255.255.255.0", "gateway": "192.168.1.1", "vlan": 10}],
        "interfaces": [{"name": "Gi0/0.10", "ip": "192.168.10.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 10}],
    }
    findings = check_gateway_mismatch(snapshot)
    assert len(findings) == 1
    assert findings[0].details["expected_gateway"] == "192.168.10.1"


def test_gateway_mismatch_clean_when_correct():
    snapshot = {
        "hosts": [{"name": "PC1", "ip": "192.168.10.10", "mask": "255.255.255.0", "gateway": "192.168.10.1", "vlan": 10}],
        "interfaces": [{"name": "Gi0/0.10", "ip": "192.168.10.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 10}],
    }
    assert check_gateway_mismatch(snapshot) == []


def test_gateway_mismatch_falls_back_to_subnet_sanity_check():
    # No known router interface for this VLAN -> fall back to "is the
    # gateway even in the host's own subnet?"
    snapshot = {"hosts": [{"name": "PC1", "ip": "192.168.10.10", "mask": "255.255.255.0", "gateway": "10.0.0.1", "vlan": 99}]}
    findings = check_gateway_mismatch(snapshot)
    assert len(findings) == 1
    assert findings[0].details["configured_gateway"] == "10.0.0.1"


# ---------------------------------------------------------------------------
# 4. interface_down
# ---------------------------------------------------------------------------

def test_interface_down_detected():
    snapshot = {"interfaces": [{"name": "Gi0/1", "status": "administratively down", "protocol": "down"}]}
    findings = check_interface_down(snapshot)
    assert len(findings) == 1
    assert findings[0].details["interface"] == "Gi0/1"


def test_interface_partial_up_down_detected():
    snapshot = {"interfaces": [{"name": "Gi0/1.10", "status": "up", "protocol": "down"}]}
    findings = check_interface_down(snapshot)
    assert len(findings) == 1


def test_interface_up_up_is_clean():
    snapshot = {"interfaces": [{"name": "Gi0/1", "status": "up", "protocol": "up"}]}
    assert check_interface_down(snapshot) == []


# ---------------------------------------------------------------------------
# 5. missing_vlan
# ---------------------------------------------------------------------------

def test_missing_vlan_detected():
    snapshot = {
        "vlans": [{"id": 1, "name": "default"}],
        "switchports": [{"port": "Fa0/5", "vlan": 10, "mode": "access"}],
    }
    findings = check_missing_vlan(snapshot)
    assert len(findings) == 1
    assert findings[0].details["vlan"] == 10


def test_missing_vlan_clean_when_present():
    snapshot = {
        "vlans": [{"id": 10, "name": "DATA"}],
        "switchports": [{"port": "Fa0/5", "vlan": 10, "mode": "access"}],
    }
    assert check_missing_vlan(snapshot) == []


# ---------------------------------------------------------------------------
# 6. missing_route
# ---------------------------------------------------------------------------

def test_missing_route_detected():
    snapshot = {
        "routes": [{"network": "192.168.10.0", "mask": "255.255.255.0"}],
        "required_routes": [{"network": "192.168.30.0", "mask": "255.255.255.0", "reason": "server subnet"}],
    }
    findings = check_missing_route(snapshot)
    assert len(findings) == 1
    assert findings[0].details["network"] == "192.168.30.0"


def test_missing_route_clean_when_present():
    snapshot = {
        "routes": [{"network": "192.168.30.0", "mask": "255.255.255.0"}],
        "required_routes": [{"network": "192.168.30.0", "mask": "255.255.255.0", "reason": "server subnet"}],
    }
    assert check_missing_route(snapshot) == []


# ---------------------------------------------------------------------------
# run_all_checks combinator
# ---------------------------------------------------------------------------

def test_run_all_checks_combines_multiple_issues():
    snapshot = {
        "hosts": [{"name": "PC1", "ip": "10.0.0.5", "mask": "255.255.255.0", "gateway": "10.0.0.5", "vlan": 5}],
        "interfaces": [{"name": "Gi0/1", "status": "down", "protocol": "down"}],
        "vlans": [],
        "switchports": [{"port": "Fa0/1", "vlan": 5, "mode": "access"}],
        "routes": [],
        "required_routes": [{"network": "8.8.8.0", "mask": "255.255.255.0", "reason": "test"}],
    }
    findings = run_all_checks(snapshot)
    checks_found = {f.check for f in findings}
    assert "interface_down" in checks_found
    assert "missing_vlan" in checks_found
    assert "missing_route" in checks_found


def test_run_all_checks_clean_snapshot_returns_nothing():
    snapshot = {
        "hosts": [{"name": "PC1", "ip": "192.168.10.10", "mask": "255.255.255.0", "gateway": "192.168.10.1", "vlan": 10}],
        "interfaces": [{"name": "Gi0/0.10", "ip": "192.168.10.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 10}],
        "vlans": [{"id": 10, "name": "DATA"}],
        "switchports": [{"port": "Fa0/1", "vlan": 10, "mode": "access"}],
        "routes": [{"network": "192.168.10.0", "mask": "255.255.255.0"}],
        "required_routes": [{"network": "192.168.10.0", "mask": "255.255.255.0", "reason": "n/a"}],
    }
    assert run_all_checks(snapshot) == []


# ---------------------------------------------------------------------------
# Integration test against the full case dataset
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not os.path.exists(CASES_CSV), reason="cases.csv not generated yet")
def test_checker_agrees_with_ground_truth_on_full_dataset():
    results = run_checks_on_cases_csv(CASES_CSV)
    assert len(results) >= 30, "Expect at least 30 cases per project requirement"

    mismatches = []
    for r in results:
        actually_flagged = r["num_findings"] > 0
        expected_flagged = r["expected_checker_findable"]
        if actually_flagged != expected_flagged:
            mismatches.append((r["case_id"], expected_flagged, actually_flagged, r["findings"]))

    assert not mismatches, f"Checker disagreed with ground truth on: {mismatches}"


@pytest.mark.skipif(not os.path.exists(CASES_CSV), reason="cases.csv not generated yet")
def test_at_least_five_cases_are_checker_findable():
    """Sanity check tied to the project's 'Responsible AI log: at least 5
    corrected cases' spirit -- we need enough deterministic signal to work with."""
    results = run_checks_on_cases_csv(CASES_CSV)
    flagged = [r for r in results if r["num_findings"] > 0]
    assert len(flagged) >= 5


@pytest.mark.skipif(not os.path.exists(CASES_CSV), reason="cases.csv not generated yet")
def test_all_six_checks_are_exercised_by_the_dataset():
    """Every one of the six deterministic checks should fire on at least
    one case in the dataset -- otherwise a rule could be silently broken
    and nothing would catch it."""
    results = run_checks_on_cases_csv(CASES_CSV)
    all_checks_seen = set()
    for r in results:
        for f in r["findings"]:
            all_checks_seen.add(f["check"])

    expected = {
        "duplicate_ip", "wrong_subnet_mask", "gateway_mismatch",
        "interface_down", "missing_vlan", "missing_route",
    }
    missing = expected - all_checks_seen
    assert not missing, f"These checks never fired on any case: {missing}"
