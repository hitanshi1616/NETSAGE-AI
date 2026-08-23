#!/usr/bin/env python3
"""
generate_cases.py
------------------
Builds cases.csv for NetSage AI (Phase 1).

Each case contains:
  - Human-readable symptom + topology note (what the junior engineer sees)
  - Realistic Cisco show-command output (the "evidence" the AI must quote/reference)
  - expected_fault / osi_layer / concept_tag / severity (ground truth for grading)
  - network_snapshot_json: a small structured extraction of the same evidence,
    used by checker.py to run deterministic rule checks (duplicate IP, wrong
    mask, gateway mismatch, interface down, missing VLAN, missing route).

Run:  python3 generate_cases.py
Output: cases.csv (in the same folder)
"""

import csv
import json
import os

CASES = []


def add_case(
    case_id, category, concept_tag, severity, osi_layer,
    symptom, topology_note, show_output, expected_fault,
    expected_next_command, snapshot, checker_findable, checker_check
):
    CASES.append({
        "case_id": case_id,
        "category": category,
        "concept_tag": concept_tag,
        "severity": severity,
        "osi_layer": osi_layer,
        "symptom": symptom.strip(),
        "topology_note": topology_note.strip(),
        "show_output": show_output.strip("\n"),
        "expected_fault": expected_fault.strip(),
        "expected_next_command": expected_next_command.strip(),
        "network_snapshot_json": json.dumps(snapshot, separators=(",", ":")),
        "checker_findable": checker_findable,
        "checker_check": checker_check,
    })


# ---------------------------------------------------------------------------
# VLAN cases (6)
# ---------------------------------------------------------------------------

add_case(
    "C001", "VLAN", "missing-vlan-on-switch", "High", "Layer 2",
    symptom="PC1 in the Sales office cannot get an IP from DHCP and cannot ping its default gateway.",
    topology_note="PC1 -> Fa0/5 on SW1 (configured for VLAN 10) -> trunk to SW-Core -> Router-on-a-stick.",
    show_output="""
SW1#show interfaces fa0/5 switchport
Name: Fa0/5
Switchport: Enabled
Administrative Mode: static access
Operational Mode: static access
Access Mode VLAN: 10 (Inactive)
Trunking Native Mode VLAN: 1 (default)

SW1#show vlan brief
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active    Fa0/1, Fa0/2, Fa0/3, Fa0/4
20   ENGINEERING                      active    Fa0/6, Fa0/7
""",
    expected_fault="VLAN 10 is assigned to Fa0/5 but does not exist in the VLAN database, so the port is inactive and PC1 is isolated at Layer 2.",
    expected_next_command="show vlan brief; vlan 10 (global config) then name SALES",
    snapshot={
        "hosts": [{"name": "PC1", "ip": None, "mask": None, "gateway": None, "vlan": 10}],
        "interfaces": [],
        "vlans": [{"id": 1, "name": "default"}, {"id": 20, "name": "ENGINEERING"}],
        "switchports": [{"port": "Fa0/5", "vlan": 10, "mode": "access"}],
        "routes": [],
        "required_routes": [],
    },
    checker_findable=True, checker_check="missing_vlan",
)

add_case(
    "C002", "VLAN", "trunk-native-vlan-mismatch", "Medium", "Layer 2",
    symptom="Devices on VLAN 30 across two switches intermittently lose connectivity and CDP reports native VLAN warnings.",
    topology_note="SW1 Gi0/1 trunk to SW2 Gi0/1, both carrying VLAN 10/20/30.",
    show_output="""
SW1#show interfaces gi0/1 trunk
Port        Mode         Encapsulation  Status        Native vlan
Gi0/1       on           802.1q         trunking      1

SW2#show interfaces gi0/1 trunk
Port        Mode         Encapsulation  Status        Native vlan
Gi0/1       on           802.1q         trunking      99

%CDP-4-NATIVE_VLAN_MISMATCH: Native VLAN mismatch discovered on Gi0/1 (1), with SW2 Gi0/1 (99)
""",
    expected_fault="Native VLAN mismatch between the two trunk ends (VLAN 1 vs VLAN 99) causes untagged traffic to leak between VLANs.",
    expected_next_command="show interfaces trunk on both switches; correct native vlan to match",
    snapshot={
        "hosts": [], "interfaces": [], "vlans": [{"id": 1, "name": "default"}, {"id": 10, "name": "DATA"},
        {"id": 20, "name": "VOICE"}, {"id": 30, "name": "MGMT"}],
        "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=False, checker_check="none (trunk/native-vlan mismatch, not covered by the 6 rules)",
)

add_case(
    "C003", "VLAN", "wrong-vlan-assigned", "Medium", "Layer 2",
    symptom="A finance PC was moved to a new cube; it now lands on the guest VLAN and cannot reach the finance server.",
    topology_note="PC (finance) -> Fa0/12 on SW3, previously access vlan 40 (FINANCE), currently access vlan 50 (GUEST).",
    show_output="""
SW3#show interfaces fa0/12 switchport
Name: Fa0/12
Switchport: Enabled
Administrative Mode: static access
Operational Mode: static access
Access Mode VLAN: 50 (GUEST)
Voice VLAN: none

SW3#show vlan brief
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
40   FINANCE                          active    Fa0/10, Fa0/11
50   GUEST                            active    Fa0/12, Fa0/13
""",
    expected_fault="Port Fa0/12 is administratively assigned to VLAN 50 (GUEST) instead of VLAN 40 (FINANCE); VLAN exists but is the wrong one for this host.",
    expected_next_command="show interfaces fa0/12 switchport; switchport access vlan 40",
    snapshot={
        "hosts": [], "interfaces": [],
        "vlans": [{"id": 40, "name": "FINANCE"}, {"id": 50, "name": "GUEST"}],
        "switchports": [{"port": "Fa0/12", "vlan": 50, "mode": "access"}],
        "routes": [], "required_routes": [],
    },
    checker_findable=False, checker_check="none (VLAN exists, just the wrong one - requires intent/config comparison, not a deterministic rule)",
)

add_case(
    "C004", "VLAN", "missing-vlan-on-switch", "High", "Layer 2",
    symptom="New Voice VLAN 60 was configured on the router subinterface but phones on SW4 never get an IP.",
    topology_note="IP Phone -> Fa0/8 on SW4 (voice vlan 60 configured) -> trunk to Router subinterface Gi0/0.60.",
    show_output="""
SW4#show interfaces fa0/8 switchport
Name: Fa0/8
Administrative Mode: static access
Operational Mode: static access
Access Mode VLAN: 1 (default)
Voice VLAN: 60 (Inactive)

SW4#show vlan brief
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active    Fa0/8, Fa0/9
""",
    expected_fault="Voice VLAN 60 is referenced on the port but was never created in the VLAN database, so voice traffic has no VLAN to join.",
    expected_next_command="show vlan brief; vlan 60 (global config) then name VOICE",
    snapshot={
        "hosts": [], "interfaces": [],
        "vlans": [{"id": 1, "name": "default"}],
        "switchports": [{"port": "Fa0/8", "vlan": 60, "mode": "voice"}],
        "routes": [], "required_routes": [],
    },
    checker_findable=True, checker_check="missing_vlan",
)

add_case(
    "C005", "VLAN", "access-port-set-as-trunk", "Medium", "Layer 2",
    symptom="A single desktop PC receives traffic from multiple VLANs and its NIC shows constant broadcast flooding.",
    topology_note="PC1 -> Fa0/15 on SW2, port was accidentally left in trunk mode after a template copy.",
    show_output="""
SW2#show interfaces fa0/15 switchport
Name: Fa0/15
Switchport: Enabled
Administrative Mode: trunk
Operational Mode: trunk
Trunking VLANs Enabled: ALL
""",
    expected_fault="Fa0/15 is operating as a trunk instead of an access port, so PC1 receives tagged traffic from every VLAN allowed on the trunk.",
    expected_next_command="show interfaces fa0/15 switchport; switchport mode access; switchport access vlan <id>",
    snapshot={
        "hosts": [], "interfaces": [], "vlans": [{"id": 1, "name": "default"}],
        "switchports": [{"port": "Fa0/15", "vlan": None, "mode": "trunk"}],
        "routes": [], "required_routes": [],
    },
    checker_findable=False, checker_check="none (port mode issue, not one of the 6 rules)",
)

add_case(
    "C006", "VLAN", "missing-vlan-on-switch", "High", "Layer 2",
    symptom="Lab PC3 cannot obtain an IP address at all; link light is on but the port never comes fully up in Packet Tracer.",
    topology_note="PC3 -> Fa0/18 on SW5, assigned to VLAN 25 (LAB) which was deleted during cleanup but never removed from the port.",
    show_output="""
SW5#show interfaces fa0/18 switchport
Name: Fa0/18
Administrative Mode: static access
Operational Mode: static access
Access Mode VLAN: 25 (Inactive)

SW5#show vlan brief
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active
""",
    expected_fault="VLAN 25 was removed from the VLAN database but Fa0/18 is still assigned to it, leaving the port inactive.",
    expected_next_command="show vlan brief; vlan 25 (recreate) or move port with switchport access vlan 1",
    snapshot={
        "hosts": [], "interfaces": [],
        "vlans": [{"id": 1, "name": "default"}],
        "switchports": [{"port": "Fa0/18", "vlan": 25, "mode": "access"}],
        "routes": [], "required_routes": [],
    },
    checker_findable=True, checker_check="missing_vlan",
)

# ---------------------------------------------------------------------------
# Gateway / IP addressing cases (6)
# ---------------------------------------------------------------------------

add_case(
    "C007", "Gateway", "gateway-mismatch", "High", "Layer 3",
    symptom="PC2 gets an IP via DHCP but cannot ping anything outside its own subnet, including the router.",
    topology_note="PC2 on VLAN 10, subnet 192.168.10.0/24. Router subinterface Gi0/0.10 is 192.168.10.1.",
    show_output="""
PC2> ipconfig
IP Address......: 192.168.10.55
Subnet Mask......: 255.255.255.0
Default Gateway...: 192.168.1.1

Router#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0.10  192.168.10.1    YES manual up                    up
""",
    expected_fault="PC2's default gateway (192.168.1.1) is not the router's VLAN 10 interface address (192.168.10.1) and is not even in the same subnet as the PC.",
    expected_next_command="ipconfig /all on PC2; correct default gateway to 192.168.10.1",
    snapshot={
        "hosts": [{"name": "PC2", "ip": "192.168.10.55", "mask": "255.255.255.0", "gateway": "192.168.1.1", "vlan": 10}],
        "interfaces": [{"name": "Gi0/0.10", "ip": "192.168.10.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 10}],
        "vlans": [{"id": 10, "name": "DATA"}],
        "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=True, checker_check="gateway_mismatch",
)

add_case(
    "C008", "Gateway", "wrong-subnet-mask", "High", "Layer 3",
    symptom="PC4 can ping its own gateway but cannot reach a server that is only 10 addresses away in the same VLAN.",
    topology_note="PC4 and Server1 are both on VLAN 20, intended subnet 192.168.20.0/24.",
    show_output="""
PC4> ipconfig
IP Address......: 192.168.20.40
Subnet Mask......: 255.255.255.192
Default Gateway...: 192.168.20.1

Router#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0.20  192.168.20.1    YES manual up                    up
""",
    expected_fault="PC4 uses a /26 mask (255.255.255.192) while the VLAN 20 interface uses /24 (255.255.255.0), so PC4 calculates a smaller subnet and treats the server as remote.",
    expected_next_command="ipconfig /all on PC4; correct subnet mask to 255.255.255.0",
    snapshot={
        "hosts": [{"name": "PC4", "ip": "192.168.20.40", "mask": "255.255.255.192", "gateway": "192.168.20.1", "vlan": 20}],
        "interfaces": [{"name": "Gi0/0.20", "ip": "192.168.20.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 20}],
        "vlans": [{"id": 20, "name": "ENGINEERING"}],
        "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=True, checker_check="wrong_subnet_mask",
)

add_case(
    "C009", "Gateway", "duplicate-ip", "High", "Layer 3",
    symptom="Two lab PCs both show intermittent 'IP address conflict' popups and one keeps dropping off the network.",
    topology_note="PC5 and PC6 are both configured statically on VLAN 10.",
    show_output="""
PC5> ipconfig
IP Address......: 192.168.10.20
Subnet Mask......: 255.255.255.0
Default Gateway...: 192.168.10.1

PC6> ipconfig
IP Address......: 192.168.10.20
Subnet Mask......: 255.255.255.0
Default Gateway...: 192.168.10.1

%DUPADDR-3-DUPLICATE_ADDRESS: Duplicate address 192.168.10.20 on VLAN10
""",
    expected_fault="PC5 and PC6 are both statically configured with 192.168.10.20, causing an IP address conflict on VLAN 10.",
    expected_next_command="ipconfig on both hosts; reassign PC6 to an unused address such as 192.168.10.21",
    snapshot={
        "hosts": [
            {"name": "PC5", "ip": "192.168.10.20", "mask": "255.255.255.0", "gateway": "192.168.10.1", "vlan": 10},
            {"name": "PC6", "ip": "192.168.10.20", "mask": "255.255.255.0", "gateway": "192.168.10.1", "vlan": 10},
        ],
        "interfaces": [{"name": "Gi0/0.10", "ip": "192.168.10.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 10}],
        "vlans": [{"id": 10, "name": "DATA"}], "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=True, checker_check="duplicate_ip",
)

add_case(
    "C010", "Gateway", "gateway-mismatch", "Medium", "Layer 3",
    symptom="A newly imaged laptop can reach its own subnet but not the internet; it worked fine before the reimage.",
    topology_note="Laptop on VLAN 30, subnet 192.168.30.0/24, gateway should be 192.168.30.1.",
    show_output="""
Laptop> ipconfig
IP Address......: 192.168.30.75
Subnet Mask......: 255.255.255.0
Default Gateway...: 192.168.30.254

Router#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0.30  192.168.30.1    YES manual up                    up
""",
    expected_fault="The reimage left a stale gateway (192.168.30.254) that does not match the actual router VLAN 30 interface (192.168.30.1).",
    expected_next_command="ipconfig /all; correct default gateway to 192.168.30.1",
    snapshot={
        "hosts": [{"name": "Laptop", "ip": "192.168.30.75", "mask": "255.255.255.0", "gateway": "192.168.30.254", "vlan": 30}],
        "interfaces": [{"name": "Gi0/0.30", "ip": "192.168.30.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 30}],
        "vlans": [{"id": 30, "name": "MGMT"}], "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=True, checker_check="gateway_mismatch",
)

add_case(
    "C011", "Gateway", "wrong-subnet-mask", "Medium", "Layer 3",
    symptom="Half the PCs in VLAN 40 can reach each other, the other half cannot, seemingly at random.",
    topology_note="VLAN 40 subnet is 192.168.40.0/24; PC7 was set up manually and typo'd the mask.",
    show_output="""
PC7> ipconfig
IP Address......: 192.168.40.12
Subnet Mask......: 255.255.0.0
Default Gateway...: 192.168.40.1

Router#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0.40  192.168.40.1    YES manual up                    up
""",
    expected_fault="PC7 has a /16 mask (255.255.0.0) instead of the intended /24, so it computes an oversized subnet and mishandles ARP/routing decisions for peers.",
    expected_next_command="ipconfig /all on PC7; correct subnet mask to 255.255.255.0",
    snapshot={
        "hosts": [{"name": "PC7", "ip": "192.168.40.12", "mask": "255.255.0.0", "gateway": "192.168.40.1", "vlan": 40}],
        "interfaces": [{"name": "Gi0/0.40", "ip": "192.168.40.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 40}],
        "vlans": [{"id": 40, "name": "FINANCE"}], "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=True, checker_check="wrong_subnet_mask",
)

add_case(
    "C012", "Gateway", "duplicate-ip", "Medium", "Layer 3",
    symptom="A server monitoring dashboard shows the print server flapping online/offline every few minutes.",
    topology_note="Print server and a technician's laptop were both statically assigned during setup, on VLAN 20.",
    show_output="""
PrintServer> ipconfig
IP Address......: 192.168.20.99
Subnet Mask......: 255.255.255.0
Default Gateway...: 192.168.20.1

TechLaptop> ipconfig
IP Address......: 192.168.20.99
Subnet Mask......: 255.255.255.0
Default Gateway...: 192.168.20.1
""",
    expected_fault="PrintServer and TechLaptop share the same static IP (192.168.20.99), causing intermittent connectivity as devices contend for the address.",
    expected_next_command="ipconfig on both devices; reassign TechLaptop to 192.168.20.98 or another free address",
    snapshot={
        "hosts": [
            {"name": "PrintServer", "ip": "192.168.20.99", "mask": "255.255.255.0", "gateway": "192.168.20.1", "vlan": 20},
            {"name": "TechLaptop", "ip": "192.168.20.99", "mask": "255.255.255.0", "gateway": "192.168.20.1", "vlan": 20},
        ],
        "interfaces": [{"name": "Gi0/0.20", "ip": "192.168.20.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 20}],
        "vlans": [{"id": 20, "name": "ENGINEERING"}], "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=True, checker_check="duplicate_ip",
)

# ---------------------------------------------------------------------------
# DHCP cases (5)
# ---------------------------------------------------------------------------

add_case(
    "C013", "DHCP", "dhcp-scope-exhausted", "Medium", "Layer 3/7",
    symptom="New PCs added to VLAN 10 fail to get an IP address; existing PCs are fine.",
    topology_note="DHCP pool VLAN10_POOL configured with a small network range on the router.",
    show_output="""
Router#show ip dhcp pool VLAN10_POOL
Pool VLAN10_POOL :
 Utilization mark (high/low)    : 100 / 0
 Subnet size (first/last)       : 0 / 0
 Total addresses                : 14
 Leased addresses               : 14
 Excluded addresses             : 2
 Pending event                  : none

Router#show ip dhcp conflict
IP address       Detection method     Detection time
""",
    expected_fault="The DHCP pool for VLAN 10 is fully leased out (14/14 addresses), so new clients cannot obtain a lease.",
    expected_next_command="show ip dhcp pool VLAN10_POOL; widen the network statement or shorten the lease time",
    snapshot={
        "hosts": [], "interfaces": [], "vlans": [{"id": 10, "name": "DATA"}],
        "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=False, checker_check="none (pool exhaustion is not one of the 6 rules)",
)

add_case(
    "C014", "DHCP", "duplicate-ip", "High", "Layer 3",
    symptom="A statically-configured server keeps losing connectivity right after the DHCP server hands out new leases.",
    topology_note="Server1 is statically set inside the same range the DHCP pool also hands out on VLAN 10.",
    show_output="""
Server1> ipconfig
IP Address......: 192.168.10.50
Subnet Mask......: 255.255.255.0
Default Gateway...: 192.168.10.1

Router#show ip dhcp pool VLAN10_POOL
Network number/mask: 192.168.10.0 / 255.255.255.0
Excluded addresses : 192.168.10.1 - 192.168.10.10

%DHCPD-4-PING_CONFLICT: DHCP address conflict: client claims 192.168.10.50 already in use
""",
    expected_fault="192.168.10.50 was never excluded from the DHCP pool, so the server's static address is periodically re-leased to a DHCP client, creating a conflict.",
    expected_next_command="show ip dhcp pool VLAN10_POOL; add ip dhcp excluded-address 192.168.10.50 192.168.10.50",
    snapshot={
        "hosts": [
            {"name": "Server1", "ip": "192.168.10.50", "mask": "255.255.255.0", "gateway": "192.168.10.1", "vlan": 10},
            {"name": "DHCP-Client-Lease", "ip": "192.168.10.50", "mask": "255.255.255.0", "gateway": "192.168.10.1", "vlan": 10},
        ],
        "interfaces": [{"name": "Gi0/0.10", "ip": "192.168.10.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 10}],
        "vlans": [{"id": 10, "name": "DATA"}], "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=True, checker_check="duplicate_ip",
)

add_case(
    "C015", "DHCP", "dhcp-relay-missing", "High", "Layer 3",
    symptom="PCs on a remote VLAN across a router hop never receive a DHCP-assigned IP, but local-VLAN PCs work fine.",
    topology_note="DHCP server lives in VLAN 10; VLAN 50 clients are on a different subnet reachable only via the router.",
    show_output="""
Router#show run interface gi0/0.50
interface GigabitEthernet0/0.50
 encapsulation dot1Q 50
 ip address 192.168.50.1 255.255.255.0
!
(no ip helper-address configured)
""",
    expected_fault="VLAN 50's router subinterface has no 'ip helper-address' pointing at the DHCP server, so broadcast DHCP discovers never reach it across the router boundary.",
    expected_next_command="show run interface gi0/0.50; add ip helper-address 192.168.10.5",
    snapshot={
        "hosts": [], "interfaces": [], "vlans": [{"id": 50, "name": "REMOTE"}],
        "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=False, checker_check="none (missing ip helper-address is not one of the 6 rules)",
)

add_case(
    "C016", "DHCP", "wrong-subnet-mask", "Medium", "Layer 3",
    symptom="Devices in the new lab VLAN 70 get IP addresses but cannot talk to each other, only to the router.",
    topology_note="DHCP pool for VLAN 70 was created with the wrong mask in the pool definition.",
    show_output="""
Router#show run | section ip dhcp pool VLAN70_POOL
ip dhcp pool VLAN70_POOL
 network 192.168.70.0 255.255.255.128
 default-router 192.168.70.1
 dns-server 8.8.8.8

Router#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0.70  192.168.70.1    YES manual up                    up
""",
    expected_fault="The DHCP pool hands out a /25 mask (255.255.255.128) while the router interface uses /24, so half the leased hosts fall outside the router's actual subnet.",
    expected_next_command="show run | section dhcp pool VLAN70_POOL; correct network mask to 255.255.255.0",
    snapshot={
        "hosts": [{"name": "LabPC-DHCP", "ip": "192.168.70.40", "mask": "255.255.255.128", "gateway": "192.168.70.1", "vlan": 70}],
        "interfaces": [{"name": "Gi0/0.70", "ip": "192.168.70.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 70}],
        "vlans": [{"id": 70, "name": "LAB"}], "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=True, checker_check="wrong_subnet_mask",
)

add_case(
    "C017", "DHCP", "interface-down", "High", "Layer 1/2",
    symptom="An entire branch VLAN lost DHCP and internet access at the same time this morning.",
    topology_note="Router subinterface Gi0/0.80 serves VLAN 80; someone bumped the patch cable overnight.",
    show_output="""
Router#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0     unassigned      YES manual administratively down down
GigabitEthernet0/0.80  192.168.80.1    YES manual up                    down
""",
    expected_fault="The physical parent interface GigabitEthernet0/0 is administratively down, taking every subinterface (including VLAN 80's Gi0/0.80) down with it.",
    expected_next_command="show ip interface brief; no shutdown on GigabitEthernet0/0",
    snapshot={
        "hosts": [], "interfaces": [
            {"name": "Gi0/0", "ip": None, "mask": None, "status": "administratively down", "protocol": "down", "vlan": None},
            {"name": "Gi0/0.80", "ip": "192.168.80.1", "mask": "255.255.255.0", "status": "up", "protocol": "down", "vlan": 80},
        ],
        "vlans": [{"id": 80, "name": "BRANCH"}], "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=True, checker_check="interface_down",
)

# ---------------------------------------------------------------------------
# DNS cases (4) - deliberately outside the 6 deterministic checks
# ---------------------------------------------------------------------------

add_case(
    "C018", "DNS", "wrong-dns-server", "Medium", "Layer 7",
    symptom="PCs can ping the file server by IP address but 'ping fileserver.lab.local' fails to resolve.",
    topology_note="PC8 on VLAN 10 configured with a DNS server address that does not belong to this lab.",
    show_output="""
PC8> ipconfig /all
IP Address......: 192.168.10.60
Subnet Mask......: 255.255.255.0
Default Gateway...: 192.168.10.1
DNS Servers......: 203.0.113.9

PC8> ping fileserver.lab.local
Ping request could not find host fileserver.lab.local. Please check the name and try again.

PC8> ping 192.168.10.5
Reply from 192.168.10.5: bytes=32 time=1ms TTL=128
""",
    expected_fault="PC8 is pointed at an unreachable external DNS server (203.0.113.9) instead of the internal lab DNS server, so name resolution fails while IP connectivity works.",
    expected_next_command="ipconfig /all; correct DNS server to the internal lab DNS address, e.g. 192.168.10.5",
    snapshot={
        "hosts": [{"name": "PC8", "ip": "192.168.10.60", "mask": "255.255.255.0", "gateway": "192.168.10.1", "vlan": 10}],
        "interfaces": [], "vlans": [{"id": 10, "name": "DATA"}], "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=False, checker_check="none (DNS server value is not one of the 6 rules)",
)

add_case(
    "C019", "DNS", "missing-dns-record", "Low", "Layer 7",
    symptom="A newly built app server is reachable by IP but nobody can reach it by name; other hostnames resolve fine.",
    topology_note="Internal DNS server on VLAN 10; new A record for appserver.lab.local was never created.",
    show_output="""
DNSServer#show hosts | include appserver
(no output)

PC9> ping appserver.lab.local
Ping request could not find host appserver.lab.local. Please check the name and try again.

PC9> ping 192.168.10.70
Reply from 192.168.10.70: bytes=32 time=2ms TTL=128
""",
    expected_fault="No A record exists for appserver.lab.local on the internal DNS server, so resolution fails even though the host itself is reachable.",
    expected_next_command="show hosts on DNS server; add the missing A record",
    snapshot={"hosts": [], "interfaces": [], "vlans": [], "switchports": [], "routes": [], "required_routes": []},
    checker_findable=False, checker_check="none (missing DNS record is not one of the 6 rules)",
)

add_case(
    "C020", "DNS", "dns-server-unreachable", "Medium", "Layer 3/7",
    symptom="All name resolution in VLAN 20 stopped working after a routing change this morning.",
    topology_note="DNS server sits in VLAN 10; VLAN 20 lost its route to VLAN 10 in an unrelated change.",
    show_output="""
PC10> nslookup fileserver.lab.local
DNS request timed out.

Router#show ip route | include 192.168.10.0
(no output)
""",
    expected_fault="VLAN 20 has no route back to the VLAN 10 subnet where the DNS server lives, so DNS queries time out even though the DNS config on the PC is correct.",
    expected_next_command="show ip route; restore the route to 192.168.10.0/24",
    snapshot={
        "hosts": [], "interfaces": [], "vlans": [{"id": 10, "name": "DATA"}, {"id": 20, "name": "ENGINEERING"}],
        "switchports": [],
        "routes": [],
        "required_routes": [{"network": "192.168.10.0", "mask": "255.255.255.0", "reason": "DNS server subnet must be reachable from VLAN 20"}],
    },
    checker_findable=True, checker_check="missing_route",
)

add_case(
    "C021", "DNS", "hosts-file-override", "Low", "Layer 7",
    symptom="One technician's laptop resolves fileserver.lab.local to the wrong (decommissioned) IP address while every other PC resolves correctly.",
    topology_note="Local hosts file on the technician laptop has a stale static entry.",
    show_output="""
TechLaptop> ping fileserver.lab.local
Pinging 192.168.10.9 with 32 bytes of data:
Request timed out.

DNSServer#show hosts | include fileserver
fileserver.lab.local            192.168.10.5
""",
    expected_fault="The technician's laptop has a stale local hosts-file entry (192.168.10.9) overriding the correct DNS answer (192.168.10.5) from the lab DNS server.",
    expected_next_command="inspect local hosts file on TechLaptop; remove the stale static entry",
    snapshot={"hosts": [], "interfaces": [], "vlans": [], "switchports": [], "routes": [], "required_routes": []},
    checker_findable=False, checker_check="none (client-side hosts file is not one of the 6 rules)",
)

# ---------------------------------------------------------------------------
# Routing cases (6)
# ---------------------------------------------------------------------------

add_case(
    "C022", "Routing", "missing-route", "High", "Layer 3",
    symptom="PC gets an IP but cannot reach server in VLAN 30; gateway ping works.",
    topology_note="Example case from the problem statement: inter-VLAN routing to the VLAN 30 server subnet.",
    show_output="""
PC> ping 192.168.10.1
Reply from 192.168.10.1: bytes=32 time=1ms TTL=255

PC> ping 192.168.30.50
Request timed out.

Router#show ip route
     192.168.10.0/24 is directly connected, GigabitEthernet0/0.10
     192.168.20.0/24 is directly connected, GigabitEthernet0/0.20

Router#show access-lists
(no access-lists configured)
""",
    expected_fault="Likely inter-VLAN routing issue at Layer 3: the router has no route/subinterface for the 192.168.30.0/24 (VLAN 30) server subnet, so traffic beyond the gateway is dropped.",
    expected_next_command="show ip route; show access-lists; show interfaces trunk",
    snapshot={
        "hosts": [], "interfaces": [
            {"name": "Gi0/0.10", "ip": "192.168.10.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 10},
            {"name": "Gi0/0.20", "ip": "192.168.20.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 20},
        ],
        "vlans": [{"id": 10, "name": "DATA"}, {"id": 20, "name": "ENGINEERING"}, {"id": 30, "name": "SERVERS"}],
        "switchports": [],
        "routes": [{"network": "192.168.10.0", "mask": "255.255.255.0"}, {"network": "192.168.20.0", "mask": "255.255.255.0"}],
        "required_routes": [{"network": "192.168.30.0", "mask": "255.255.255.0", "reason": "VLAN 30 server subnet must be reachable"}],
    },
    checker_findable=True, checker_check="missing_route",
)

add_case(
    "C023", "Routing", "missing-route", "High", "Layer 3",
    symptom="Branch office PCs can reach HQ but HQ users report they cannot reach the branch file share.",
    topology_note="Static routing between HQ router and Branch router; return route was never added at HQ.",
    show_output="""
HQ-Router#show ip route static
     10.10.20.0/24 [1/0] via 10.10.0.2

BranchRouter#show ip route static
(no output)
""",
    expected_fault="HQ has a static route to the branch (10.10.20.0/24) but the branch router has no return static route back to HQ's subnet, so replies are dropped at the branch.",
    expected_next_command="show ip route static on both routers; add missing return route on BranchRouter",
    snapshot={
        "hosts": [], "interfaces": [], "vlans": [], "switchports": [],
        "routes": [],
        "required_routes": [{"network": "10.10.0.0", "mask": "255.255.255.0", "reason": "HQ subnet must be reachable from Branch"}],
    },
    checker_findable=True, checker_check="missing_route",
)

add_case(
    "C024", "Routing", "ospf-neighbor-down", "High", "Layer 3",
    symptom="Two core routers stopped exchanging routes after an interface renumbering; remote VLANs are unreachable.",
    topology_note="R1 and R2 run OSPF area 0 over a /30 link; R2's interface was renumbered without updating R1.",
    show_output="""
R1#show ip ospf neighbor
(no output)

R1#show run interface gi0/1
interface GigabitEthernet0/1
 ip address 10.0.0.1 255.255.255.252
 ip ospf 1 area 0

R2#show run interface gi0/1
interface GigabitEthernet0/1
 ip address 10.0.1.1 255.255.255.252
 ip ospf 1 area 0
""",
    expected_fault="R1 and R2 no longer share the same subnet on their OSPF link (10.0.0.0/30 vs 10.0.1.0/30), so the adjacency cannot form and routes stop propagating.",
    expected_next_command="show ip ospf neighbor; show run interface gi0/1 on both routers; align IPs onto the same /30",
    snapshot={"hosts": [], "interfaces": [], "vlans": [], "switchports": [], "routes": [], "required_routes": []},
    checker_findable=False, checker_check="none (OSPF adjacency mismatch is not one of the 6 rules)",
)

add_case(
    "C025", "Routing", "missing-route", "Medium", "Layer 3",
    symptom="A new server VLAN 90 was stood up; local PCs can reach it, but no other VLAN can reach the new server.",
    topology_note="Router-on-a-stick with subinterface Gi0/0.90 created, but connected route did not propagate as expected (no dynamic routing enabled).",
    show_output="""
Router#show run interface gi0/0.90
interface GigabitEthernet0/0.90
 encapsulation dot1Q 90
 ip address 192.168.90.1 255.255.255.0

Router#show ip route
     192.168.10.0/24 is directly connected, GigabitEthernet0/0.10
     192.168.20.0/24 is directly connected, GigabitEthernet0/0.20
""",
    expected_fault="Subinterface Gi0/0.90 exists and is configured, but 'show ip route' does not list 192.168.90.0/24 as connected -- the interface is likely down/down or the encapsulation/VLAN tag does not match the switch trunk, so the route never installs.",
    expected_next_command="show ip interface brief; show ip route; verify trunk allows vlan 90",
    snapshot={
        "hosts": [], "interfaces": [
            {"name": "Gi0/0.10", "ip": "192.168.10.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 10},
            {"name": "Gi0/0.20", "ip": "192.168.20.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 20},
            {"name": "Gi0/0.90", "ip": "192.168.90.1", "mask": "255.255.255.0", "status": "down", "protocol": "down", "vlan": 90},
        ],
        "vlans": [{"id": 10, "name": "DATA"}, {"id": 20, "name": "ENGINEERING"}, {"id": 90, "name": "NEWSERVERS"}],
        "switchports": [],
        "routes": [{"network": "192.168.10.0", "mask": "255.255.255.0"}, {"network": "192.168.20.0", "mask": "255.255.255.0"}],
        "required_routes": [{"network": "192.168.90.0", "mask": "255.255.255.0", "reason": "new server VLAN must be reachable from other VLANs"}],
    },
    checker_findable=True, checker_check="missing_route,interface_down",
)

add_case(
    "C026", "Routing", "default-route-missing", "Medium", "Layer 3",
    symptom="Internal VLANs can reach each other fine but nobody can reach the internet or ping 8.8.8.8.",
    topology_note="Edge router connects to ISP; no default route configured after a recent router replacement.",
    show_output="""
EdgeRouter#show ip route
     192.168.10.0/24 is directly connected, GigabitEthernet0/0.10
     192.168.20.0/24 is directly connected, GigabitEthernet0/0.20
     (no S* 0.0.0.0/0 entry)

PC> ping 8.8.8.8
Request timed out.
""",
    expected_fault="The edge router has no default route (0.0.0.0/0) toward the ISP, so all internet-bound traffic is dropped even though internal routing works.",
    expected_next_command="show ip route; ip route 0.0.0.0 0.0.0.0 <ISP next-hop>",
    snapshot={
        "hosts": [], "interfaces": [], "vlans": [{"id": 10, "name": "DATA"}, {"id": 20, "name": "ENGINEERING"}],
        "switchports": [],
        "routes": [{"network": "192.168.10.0", "mask": "255.255.255.0"}, {"network": "192.168.20.0", "mask": "255.255.255.0"}],
        "required_routes": [{"network": "0.0.0.0", "mask": "0.0.0.0", "reason": "default route to ISP required for internet access"}],
    },
    checker_findable=True, checker_check="missing_route",
)

add_case(
    "C027", "Routing", "interface-down", "High", "Layer 1",
    symptom="An entire remote VLAN dropped off the network after routine maintenance overnight.",
    topology_note="Trunk-facing router interface Gi0/1 was left shut down after a maintenance window.",
    show_output="""
Router#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/1     unassigned      YES manual administratively down down
GigabitEthernet0/1.100 192.168.100.1   YES manual up                    down
""",
    expected_fault="GigabitEthernet0/1 is administratively shut down, taking VLAN 100's subinterface down with it and isolating that entire VLAN.",
    expected_next_command="show ip interface brief; no shutdown on GigabitEthernet0/1",
    snapshot={
        "hosts": [], "interfaces": [
            {"name": "Gi0/1", "ip": None, "mask": None, "status": "administratively down", "protocol": "down", "vlan": None},
            {"name": "Gi0/1.100", "ip": "192.168.100.1", "mask": "255.255.255.0", "status": "up", "protocol": "down", "vlan": 100},
        ],
        "vlans": [{"id": 100, "name": "REMOTE"}], "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=True, checker_check="interface_down",
)

# ---------------------------------------------------------------------------
# ACL cases (4) - deliberately outside the 6 deterministic checks
# ---------------------------------------------------------------------------

add_case(
    "C028", "ACL", "acl-blocks-legit-traffic", "High", "Layer 3/4",
    symptom="PC in VLAN 10 can ping the VLAN 30 server but web browsing to the server fails.",
    topology_note="An ACL applied inbound on the router's VLAN 30 subinterface restricts traffic.",
    show_output="""
Router#show access-lists
Extended IP access list SERVER_ACL
    10 permit icmp any any
    20 deny tcp any any eq 80
    30 permit ip any any

Router#show run interface gi0/0.30
interface GigabitEthernet0/0.30
 ip address 192.168.30.1 255.255.255.0
 ip access-group SERVER_ACL in
""",
    expected_fault="ACL 'SERVER_ACL' explicitly denies TCP port 80 before the permit-any statement, blocking HTTP while allowing ICMP (ping) through.",
    expected_next_command="show access-lists; show run interface gi0/0.30; add/reorder a permit for tcp eq 80",
    snapshot={"hosts": [], "interfaces": [], "vlans": [], "switchports": [], "routes": [], "required_routes": []},
    checker_findable=False, checker_check="none (ACL rule-order logic is not one of the 6 rules)",
)

add_case(
    "C029", "ACL", "acl-wildcard-mistake", "Medium", "Layer 3",
    symptom="Only some PCs in VLAN 20 can reach the internet; others in the same subnet are blocked entirely.",
    topology_note="An ACL uses an incorrect wildcard mask that only covers half the subnet.",
    show_output="""
Router#show access-lists
Standard IP access list INTERNET_OUT
    10 permit 192.168.20.0 0.0.0.63

Router#show run interface gi0/1
interface GigabitEthernet0/1
 ip access-group INTERNET_OUT out
""",
    expected_fault="The ACL wildcard mask (0.0.0.63) only covers 192.168.20.0-192.168.20.63, a quarter of the actual /24 subnet, so hosts above .63 are silently denied by the implicit deny.",
    expected_next_command="show access-lists; correct wildcard mask to 0.0.0.255",
    snapshot={"hosts": [], "interfaces": [], "vlans": [], "switchports": [], "routes": [], "required_routes": []},
    checker_findable=False, checker_check="none (ACL wildcard math is not one of the 6 rules)",
)

add_case(
    "C030", "ACL", "acl-blocks-legit-traffic", "Medium", "Layer 3/4",
    symptom="Guest Wi-Fi can reach internal server.",
    topology_note="Example from problem statement: guest isolation ACL is missing or misapplied on the guest VLAN interface.",
    show_output="""
Router#show run interface gi0/0.50
interface GigabitEthernet0/0.50
 description GUEST_WIFI
 ip address 192.168.50.1 255.255.255.0
 (no ip access-group applied)

Router#show access-lists
(no access-lists configured)
""",
    expected_fault="Likely guest isolation failure / security issue: no ACL is applied to the guest VLAN interface, so guest traffic is not restricted from reaching internal servers.",
    expected_next_command="show run interface gi0/0.50; inspect VLAN mapping and ACL rules; apply a guest-isolation ACL",
    snapshot={"hosts": [], "interfaces": [], "vlans": [{"id": 50, "name": "GUEST"}], "switchports": [], "routes": [], "required_routes": []},
    checker_findable=False, checker_check="none (missing ACL application is not one of the 6 rules)",
)

add_case(
    "C031", "ACL", "acl-applied-wrong-direction", "Medium", "Layer 3",
    symptom="Server in VLAN 30 cannot initiate connections out, but inbound connections to it work fine.",
    topology_note="ACL meant to restrict inbound traffic was applied in the outbound direction instead.",
    show_output="""
Router#show run interface gi0/0.30
interface GigabitEthernet0/0.30
 ip address 192.168.30.1 255.255.255.0
 ip access-group RESTRICT_IN out

Router#show access-lists
Extended IP access list RESTRICT_IN
    10 deny tcp any any eq 443
    20 permit ip any any
""",
    expected_fault="ACL 'RESTRICT_IN' was intended to filter inbound traffic but is applied with 'out', so it instead blocks the server's own outbound HTTPS traffic.",
    expected_next_command="show run interface gi0/0.30; change ip access-group RESTRICT_IN to 'in'",
    snapshot={"hosts": [], "interfaces": [], "vlans": [], "switchports": [], "routes": [], "required_routes": []},
    checker_findable=False, checker_check="none (ACL direction is not one of the 6 rules)",
)

# ---------------------------------------------------------------------------
# NAT cases (4) - deliberately outside the 6 deterministic checks
# ---------------------------------------------------------------------------

add_case(
    "C032", "NAT", "nat-not-enabled-on-interface", "High", "Layer 3",
    symptom="Internal PCs have a default route to the internet edge router but no internal host can reach any public website.",
    topology_note="NAT overload configured globally but the inside/outside interfaces were never marked.",
    show_output="""
Router#show ip nat translations
(no output)

Router#show run | include ip nat
ip nat inside source list 1 interface GigabitEthernet0/1 overload
""",
    expected_fault="NAT overload is configured but neither the inside VLAN interface nor GigabitEthernet0/1 is marked with 'ip nat inside'/'ip nat outside', so no translations ever occur.",
    expected_next_command="show ip nat translations; show run interfaces; add ip nat inside / ip nat outside",
    snapshot={"hosts": [], "interfaces": [], "vlans": [], "switchports": [], "routes": [], "required_routes": []},
    checker_findable=False, checker_check="none (missing nat inside/outside marking is not one of the 6 rules)",
)

add_case(
    "C033", "NAT", "nat-acl-too-narrow", "Medium", "Layer 3",
    symptom="PCs in VLAN 10 reach the internet fine, PCs in VLAN 20 cannot.",
    topology_note="NAT source ACL only references the VLAN 10 subnet.",
    show_output="""
Router#show run | include access-list 1
access-list 1 permit 192.168.10.0 0.0.0.255

Router#show run | include ip nat inside source
ip nat inside source list 1 interface GigabitEthernet0/1 overload
""",
    expected_fault="NAT source ACL 1 only permits the VLAN 10 subnet (192.168.10.0/24); VLAN 20 (192.168.20.0/24) is not included, so its traffic is never translated.",
    expected_next_command="show run | include access-list 1; add a permit line for 192.168.20.0 0.0.0.255",
    snapshot={"hosts": [], "interfaces": [], "vlans": [], "switchports": [], "routes": [], "required_routes": []},
    checker_findable=False, checker_check="none (NAT ACL scope is not one of the 6 rules)",
)

add_case(
    "C034", "NAT", "port-forward-misconfigured", "Low", "Layer 3/4",
    symptom="An externally hosted lab web server cannot be reached from outside the lab network on port 8080.",
    topology_note="Static NAT/port-forward rule was set up with the wrong internal port.",
    show_output="""
Router#show run | include ip nat inside source static
ip nat inside source static tcp 192.168.10.20 80 203.0.113.5 8080 extendable
""",
    expected_fault="The port-forward maps external port 8080 to internal port 80, but the web server on 192.168.10.20 is actually listening on port 8080 internally, so the translation lands on the wrong internal port.",
    expected_next_command="show run | include ip nat inside source static; verify listening port on server, correct the internal port in the NAT rule",
    snapshot={"hosts": [], "interfaces": [], "vlans": [], "switchports": [], "routes": [], "required_routes": []},
    checker_findable=False, checker_check="none (application-level port mismatch is not one of the 6 rules)",
)

add_case(
    "C035", "NAT", "nat-overload-pool-exhausted", "Medium", "Layer 3",
    symptom="Internet access works for the first several users each morning, then new connections start failing until afternoon.",
    topology_note="NAT overload configured with a very small public IP pool instead of PAT off the outside interface.",
    show_output="""
Router#show ip nat statistics
Total active translations: 1024 (0 static, 1024 dynamic)
Pool nat-pool2: netmask 255.255.255.252
    start 203.0.113.10 end 203.0.113.13
    total addresses 4, allocated 4 (100%), misses 812
""",
    expected_fault="The NAT pool only contains 4 public addresses and is fully allocated (100%, 812 misses logged), so new outbound sessions are dropped once the pool is exhausted.",
    expected_next_command="show ip nat statistics; switch to PAT overload on the outside interface or expand the pool",
    snapshot={"hosts": [], "interfaces": [], "vlans": [], "switchports": [], "routes": [], "required_routes": []},
    checker_findable=False, checker_check="none (NAT pool exhaustion is not one of the 6 rules)",
)

# ---------------------------------------------------------------------------
# Wireless cases (3)
# ---------------------------------------------------------------------------

add_case(
    "C036", "Wireless", "missing-vlan-on-switch", "Medium", "Layer 2",
    symptom="Guest Wi-Fi clients associate with the AP successfully but never get an IP address.",
    topology_note="AP trunk port on SW6 carries VLAN 55 (GUEST-WIFI) which was never created switch-side.",
    show_output="""
SW6#show interfaces fa0/24 trunk
Port        Mode         Encapsulation  Status        Native vlan
Fa0/24      on           802.1q         trunking      1

SW6#show vlan brief
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active    Fa0/1, Fa0/2
10   DATA                             active    Fa0/3, Fa0/4
""",
    expected_fault="VLAN 55 (GUEST-WIFI) is used by the AP's SSID mapping but does not exist in SW6's VLAN database, so guest traffic has nowhere to go at Layer 2.",
    expected_next_command="show vlan brief; vlan 55 (global config) then name GUEST-WIFI",
    snapshot={
        "hosts": [], "interfaces": [],
        "vlans": [{"id": 1, "name": "default"}, {"id": 10, "name": "DATA"}],
        "switchports": [{"port": "Fa0/24", "vlan": 55, "mode": "trunk-allowed"}],
        "routes": [], "required_routes": [],
    },
    checker_findable=True, checker_check="missing_vlan",
)

add_case(
    "C037", "Wireless", "interface-down", "High", "Layer 1",
    symptom="An entire floor's wireless access points went offline simultaneously.",
    topology_note="The switch uplink port feeding the wiring closet's PoE switch was shut down during a config push.",
    show_output="""
SW7#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
Vlan1                  unassigned      YES unset  administratively down down

SW7#show interfaces gi0/0/1 status
Port      Name     Status       Vlan       Duplex  Speed
Gi0/0/1            disabled     1          auto    auto
""",
    expected_fault="The uplink port Gi0/0/1 feeding the closet PoE switch (and its APs) is administratively disabled, taking the whole floor's wireless offline.",
    expected_next_command="show interfaces status; no shutdown on gi0/0/1",
    snapshot={
        "hosts": [], "interfaces": [
            {"name": "Gi0/0/1", "ip": None, "mask": None, "status": "administratively down", "protocol": "down", "vlan": 1},
        ],
        "vlans": [{"id": 1, "name": "default"}], "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=True, checker_check="interface_down",
)

add_case(
    "C038", "Wireless", "gateway-mismatch", "Medium", "Layer 3",
    symptom="A wireless laptop connects to the corporate SSID and gets an IP but cannot reach anything past the local subnet.",
    topology_note="AP maps the corporate SSID to VLAN 15; laptop received a stale gateway from a cached profile.",
    show_output="""
Laptop-WiFi> ipconfig
IP Address......: 192.168.15.88
Subnet Mask......: 255.255.255.0
Default Gateway...: 192.168.10.1

Router#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0.15  192.168.15.1    YES manual up                    up
""",
    expected_fault="The wireless laptop's cached gateway (192.168.10.1) belongs to a different VLAN; the correct VLAN 15 gateway is 192.168.15.1.",
    expected_next_command="ipconfig /all; forget/renew the wireless profile or set correct default gateway 192.168.15.1",
    snapshot={
        "hosts": [{"name": "Laptop-WiFi", "ip": "192.168.15.88", "mask": "255.255.255.0", "gateway": "192.168.10.1", "vlan": 15}],
        "interfaces": [{"name": "Gi0/0.15", "ip": "192.168.15.1", "mask": "255.255.255.0", "status": "up", "protocol": "up", "vlan": 15}],
        "vlans": [{"id": 15, "name": "CORP-WIFI"}], "switchports": [], "routes": [], "required_routes": [],
    },
    checker_findable=True, checker_check="gateway_mismatch",
)

# ---------------------------------------------------------------------------
# Write CSV
# ---------------------------------------------------------------------------

FIELDNAMES = [
    "case_id", "category", "concept_tag", "severity", "osi_layer",
    "symptom", "topology_note", "show_output", "expected_fault",
    "expected_next_command", "network_snapshot_json", "checker_findable", "checker_check",
]

if __name__ == "__main__":
    out_path = os.path.join(os.path.dirname(__file__), "cases.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for case in CASES:
            writer.writerow(case)
    print(f"Wrote {len(CASES)} cases to {out_path}")

    # quick sanity: category counts
    from collections import Counter
    counts = Counter(c["category"] for c in CASES)
    for cat, n in sorted(counts.items()):
        print(f"  {cat}: {n}")
    print(f"  TOTAL: {len(CASES)}")
