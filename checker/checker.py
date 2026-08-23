#!/usr/bin/env python3
"""
checker.py
----------
NetSage AI - Deterministic Rule Checker (Phase 1)

Runs six deterministic, rule-based checks against a structured "network
snapshot" that is extracted from a case's show-command evidence:

  1. duplicate_ip        - two hosts/interfaces sharing the same IP address
  2. wrong_subnet_mask    - a host's mask does not match its VLAN interface's mask
  3. gateway_mismatch     - a host's default gateway does not match the VLAN's
                            router interface IP (or isn't even in the same subnet)
  4. interface_down       - an interface is not both status=up and protocol=up
  5. missing_vlan         - a switchport/AP references a VLAN ID not present
                            in the VLAN database
  6. missing_route        - a required destination network is absent from the
                            routing table

These checks are intentionally deterministic (no AI/LLM call): they are meant
to run BEFORE or AFTER the AI diagnosis step as a fast, trustworthy sanity
pass, per the project's "Rule checker" requirement.

Can be used as:
  - a library:  from checker import run_all_checks
  - a CLI:      python3 checker.py --cases ../cases/cases.csv
                python3 checker.py --snapshot snapshot.json
"""

from __future__ import annotations

import argparse
import csv
import ipaddress
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    check: str
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
            "details": self.details,
        }


def _valid_ip(value: str | None) -> bool:
    if not value:
        return False
    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return False


def _valid_mask(value: str | None) -> bool:
    if not value:
        return False
    try:
        # Validate it's a proper dotted-decimal contiguous subnet mask
        ipaddress.IPv4Network(f"0.0.0.0/{value}")
        return True
    except ValueError:
        return False


def _network_of(ip: str, mask: str) -> ipaddress.IPv4Network | None:
    if not _valid_ip(ip) or not _valid_mask(mask):
        return None
    try:
        return ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Check 1: duplicate IP
# ---------------------------------------------------------------------------

def check_duplicate_ip(snapshot: dict) -> list[Finding]:
    findings: list[Finding] = []
    ip_owners: dict[str, list[str]] = defaultdict(list)

    for host in snapshot.get("hosts", []):
        ip = host.get("ip")
        if _valid_ip(ip):
            ip_owners[ip].append(host.get("name", "unknown-host"))

    for iface in snapshot.get("interfaces", []):
        ip = iface.get("ip")
        if _valid_ip(ip):
            ip_owners[ip].append(iface.get("name", "unknown-interface"))

    for ip, owners in ip_owners.items():
        if len(owners) > 1:
            findings.append(Finding(
                check="duplicate_ip",
                severity="High",
                message=f"IP address {ip} is used by more than one device: {', '.join(owners)}.",
                details={"ip": ip, "owners": owners},
            ))
    return findings


# ---------------------------------------------------------------------------
# Check 2: wrong subnet mask
# ---------------------------------------------------------------------------

def check_wrong_subnet_mask(snapshot: dict) -> list[Finding]:
    findings: list[Finding] = []

    # Build vlan -> interface mask lookup (the "source of truth" mask for a VLAN)
    vlan_iface_mask: dict[int, str] = {}
    for iface in snapshot.get("interfaces", []):
        vlan = iface.get("vlan")
        mask = iface.get("mask")
        if vlan is not None and _valid_mask(mask):
            vlan_iface_mask[vlan] = mask

    for host in snapshot.get("hosts", []):
        vlan = host.get("vlan")
        host_mask = host.get("mask")
        if vlan is None or not _valid_mask(host_mask):
            continue
        expected_mask = vlan_iface_mask.get(vlan)
        if expected_mask and host_mask != expected_mask:
            findings.append(Finding(
                check="wrong_subnet_mask",
                severity="High",
                message=(
                    f"Host {host.get('name', 'unknown-host')} uses mask {host_mask} "
                    f"but VLAN {vlan}'s router interface uses {expected_mask}."
                ),
                details={
                    "host": host.get("name"),
                    "host_mask": host_mask,
                    "expected_mask": expected_mask,
                    "vlan": vlan,
                },
            ))
    return findings


# ---------------------------------------------------------------------------
# Check 3: gateway mismatch
# ---------------------------------------------------------------------------

def check_gateway_mismatch(snapshot: dict) -> list[Finding]:
    findings: list[Finding] = []

    vlan_iface_ip: dict[int, str] = {}
    for iface in snapshot.get("interfaces", []):
        vlan = iface.get("vlan")
        ip = iface.get("ip")
        if vlan is not None and _valid_ip(ip):
            vlan_iface_ip[vlan] = ip

    for host in snapshot.get("hosts", []):
        vlan = host.get("vlan")
        gw = host.get("gateway")
        host_ip = host.get("ip")
        host_mask = host.get("mask")

        if vlan is None or not _valid_ip(gw):
            continue

        expected_gw = vlan_iface_ip.get(vlan)

        # Case A: we know the correct router interface IP for this VLAN
        if expected_gw and gw != expected_gw:
            findings.append(Finding(
                check="gateway_mismatch",
                severity="High",
                message=(
                    f"Host {host.get('name', 'unknown-host')} has default gateway {gw}, "
                    f"but VLAN {vlan}'s router interface is {expected_gw}."
                ),
                details={
                    "host": host.get("name"), "configured_gateway": gw,
                    "expected_gateway": expected_gw, "vlan": vlan,
                },
            ))
            continue

        # Case B: no known router IP for this VLAN, fall back to a sanity
        # check -- is the gateway even in the same subnet as the host?
        if not expected_gw and _valid_ip(host_ip) and _valid_mask(host_mask):
            host_net = _network_of(host_ip, host_mask)
            if host_net and ipaddress.IPv4Address(gw) not in host_net:
                findings.append(Finding(
                    check="gateway_mismatch",
                    severity="High",
                    message=(
                        f"Host {host.get('name', 'unknown-host')}'s gateway {gw} is not "
                        f"in the same subnet as its own address {host_ip}/{host_mask}."
                    ),
                    details={"host": host.get("name"), "configured_gateway": gw,
                              "host_subnet": str(host_net)},
                ))
    return findings


# ---------------------------------------------------------------------------
# Check 4: interface down
# ---------------------------------------------------------------------------

def check_interface_down(snapshot: dict) -> list[Finding]:
    findings: list[Finding] = []
    for iface in snapshot.get("interfaces", []):
        status = (iface.get("status") or "").lower()
        protocol = (iface.get("protocol") or "").lower()
        if status != "up" or protocol != "up":
            findings.append(Finding(
                check="interface_down",
                severity="High",
                message=(
                    f"Interface {iface.get('name', 'unknown')} is {status or 'unknown'}/"
                    f"{protocol or 'unknown'} (status/protocol), not up/up."
                ),
                details={"interface": iface.get("name"), "status": status, "protocol": protocol},
            ))
    return findings


# ---------------------------------------------------------------------------
# Check 5: missing VLAN
# ---------------------------------------------------------------------------

def check_missing_vlan(snapshot: dict) -> list[Finding]:
    findings: list[Finding] = []
    known_vlans = {v.get("id") for v in snapshot.get("vlans", []) if v.get("id") is not None}

    for sp in snapshot.get("switchports", []):
        vlan = sp.get("vlan")
        if vlan is None:
            continue
        if vlan not in known_vlans:
            findings.append(Finding(
                check="missing_vlan",
                severity="High",
                message=(
                    f"Port {sp.get('port', 'unknown')} references VLAN {vlan}, which does "
                    f"not exist in the VLAN database."
                ),
                details={"port": sp.get("port"), "vlan": vlan},
            ))

    # Also check host-declared VLANs that have no matching VLAN entry at all
    for host in snapshot.get("hosts", []):
        vlan = host.get("vlan")
        if vlan is not None and vlan not in known_vlans and not snapshot.get("vlans"):
            # only flag if we have literally no vlan DB and a vlan is referenced
            findings.append(Finding(
                check="missing_vlan",
                severity="Medium",
                message=(
                    f"Host {host.get('name', 'unknown-host')} is assigned to VLAN {vlan}, "
                    f"but no VLAN database was found in the evidence."
                ),
                details={"host": host.get("name"), "vlan": vlan},
            ))
    return findings


# ---------------------------------------------------------------------------
# Check 6: missing route
# ---------------------------------------------------------------------------

def check_missing_route(snapshot: dict) -> list[Finding]:
    findings: list[Finding] = []
    known_routes = set()
    for r in snapshot.get("routes", []):
        net = r.get("network")
        mask = r.get("mask")
        if net and mask:
            known_routes.add((net, mask))

    for req in snapshot.get("required_routes", []):
        net = req.get("network")
        mask = req.get("mask")
        if not net or not mask:
            continue
        if (net, mask) not in known_routes:
            findings.append(Finding(
                check="missing_route",
                severity="High",
                message=(
                    f"Required route {net}/{mask} is missing from the routing table. "
                    f"{req.get('reason', '')}".strip()
                ),
                details={"network": net, "mask": mask, "reason": req.get("reason", "")},
            ))
    return findings


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

CHECKS = {
    "duplicate_ip": check_duplicate_ip,
    "wrong_subnet_mask": check_wrong_subnet_mask,
    "gateway_mismatch": check_gateway_mismatch,
    "interface_down": check_interface_down,
    "missing_vlan": check_missing_vlan,
    "missing_route": check_missing_route,
}


def run_all_checks(snapshot: dict) -> list[Finding]:
    """Run all six deterministic checks against one network snapshot dict."""
    findings: list[Finding] = []
    for check_fn in CHECKS.values():
        findings.extend(check_fn(snapshot))
    return findings


def run_checks_on_cases_csv(csv_path: str) -> list[dict]:
    """Run the checker against every case in cases.csv. Returns a list of
    per-case result dicts, useful for the dashboard and for tests."""
    results = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                snapshot = json.loads(row["network_snapshot_json"])
            except (json.JSONDecodeError, KeyError):
                snapshot = {}
            findings = run_all_checks(snapshot)
            results.append({
                "case_id": row.get("case_id"),
                "category": row.get("category"),
                "expected_checker_findable": row.get("checker_findable", "False") == "True",
                "expected_checker_check": row.get("checker_check", ""),
                "findings": [f.to_dict() for f in findings],
                "num_findings": len(findings),
            })
    return results


def _print_report(results: list[dict]) -> None:
    total = len(results)
    flagged = sum(1 for r in results if r["num_findings"] > 0)
    print(f"Checked {total} cases -> {flagged} flagged by at least one deterministic rule.\n")
    for r in results:
        marker = "FLAGGED" if r["num_findings"] > 0 else "clean"
        print(f"[{r['case_id']}] {r['category']:<10} -> {marker} ({r['num_findings']} finding(s))")
        for f in r["findings"]:
            print(f"    - {f['check']}: {f['message']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="NetSage AI deterministic rule checker")
    parser.add_argument("--cases", help="Path to cases.csv", default=None)
    parser.add_argument("--snapshot", help="Path to a single network_snapshot JSON file", default=None)
    parser.add_argument("--json-out", help="Optional path to write full JSON results", default=None)
    args = parser.parse_args()

    if args.snapshot:
        with open(args.snapshot, encoding="utf-8") as f:
            snapshot = json.load(f)
        findings = run_all_checks(snapshot)
        for f in findings:
            print(f"- {f.check}: {f.message}")
        if not findings:
            print("No deterministic issues found.")
        return 0

    csv_path = args.cases or os.path.join(
        os.path.dirname(__file__), "..", "cases", "cases.csv"
    )
    if not os.path.exists(csv_path):
        print(f"ERROR: cases file not found at {csv_path}", file=sys.stderr)
        return 1

    results = run_checks_on_cases_csv(csv_path)
    _print_report(results)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"\nFull JSON results written to {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
