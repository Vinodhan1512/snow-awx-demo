#!/usr/bin/env python3
"""Create a demo ticket via API and follow it until the automation finishes.
Backup for the live demo, and used by setup.sh as a smoke test.

Examples:
  create_ticket.py diag
  create_ticket.py cisco --interface Loopback123 --description "Mgmt loopback CHG demo"
  create_ticket.py sg --port 8443 --cidr 10.20.0.0/16
  create_ticket.py sg --port 22 --cidr 0.0.0.0/0          # blocked by policy
  create_ticket.py sg --port 9090 --cidr 10.1.0.0/16 --simulate-failure   # shows rollback
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from demohttp import Client  # noqa: E402

if not os.environ.get("SN_INSTANCE"):          # allow running directly on Windows without sourcing .env
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from run_setup import load_env  # noqa: E402
    load_env()
E = os.environ
sn = Client(f"https://{E['SN_INSTANCE']}", user=E["SN_USERNAME"], password=E["SN_PASSWORD"])
DONE = {"success", "failed", "rejected", "rolled_back", "diagnosed", "action_required"}
PASSED = {"success", "diagnosed", "action_required", "rejected", "rolled_back"}   # the automation did its job


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["diag", "cisco", "sg"])
    ap.add_argument("--device", default=E.get("CISCO_INVENTORY_NAME", "demo-iosxe"))
    ap.add_argument("--interface", default="Loopback123",
                    help="one interface, a range (Loopback200-209) or a list (Lo200,Lo201) for cisco")
    ap.add_argument("--description", default="Configured via ServiceNow + AWX")
    ap.add_argument("--shutdown", action="store_true")
    ap.add_argument("--action", default="add", choices=["add", "remove"])
    ap.add_argument("--protocol", default="tcp", choices=["tcp", "udp"])
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--cidr", default="10.20.0.0/16")
    ap.add_argument("--simulate-failure", action="store_true")
    ap.add_argument("--no-wait", action="store_true")
    a = ap.parse_args()

    if a.kind == "diag":
        table = "incident"
        body = {"short_description": f"Interface {a.interface} reported down on {a.device}",
                "u_automation_type": "incident_diagnostics", "u_device": a.device,
                "u_interface": "GigabitEthernet1" if a.interface == "Loopback123" else a.interface}
    elif a.kind == "cisco":
        table = "change_request"
        body = {"short_description": f"Configure {a.interface} on {a.device}",
                "u_automation_type": "cisco_interface_change", "u_device": a.device,
                "u_interface": a.interface, "u_int_description": a.description,
                "u_int_shutdown": str(a.shutdown).lower()}
    else:
        table = "change_request"
        body = {"short_description": f"{a.action.title()} {a.protocol}/{a.port} from {a.cidr} on demo SG",
                "u_automation_type": "aws_sg_change", "u_sg_id": E.get("DEMO_SG_ID", ""),
                "u_sg_action": a.action, "u_sg_protocol": a.protocol, "u_sg_port": str(a.port),
                "u_sg_cidr": a.cidr, "u_simulate_failure": str(a.simulate_failure).lower()}

    rec = sn.post(f"/api/now/table/{table}", body)["result"]
    num, sid = rec["number"], rec["sys_id"]
    print(f"Created {num}: https://{E['SN_INSTANCE']}/nav_to.do?uri={table}.do?sys_id={sid}")
    if a.no_wait:
        return
    status = ""
    for _ in range(60):
        time.sleep(5)
        r = sn.get(f"/api/now/table/{table}/{sid}",
                   params={"sysparm_fields": "u_automation_status,u_awx_job"})["result"]
        if r["u_automation_status"] != status:
            status = r["u_automation_status"]
            print(f"  status: {status or '(waiting for business rule)'}  {r.get('u_awx_job', '')}")
        if status in DONE:
            break
    else:
        print("Timed out waiting. Check the ticket's work notes and the AWX job.")
        sys.exit(1)
    time.sleep(3)   # the close Business Rule runs right after the status update
    r = sn.get(f"/api/now/table/{table}/{sid}", params={"sysparm_fields": "state,close_code",
                                                        "sysparm_display_value": "true"})["result"]
    print(f"  ticket state: {r['state']}" + (f" ({r['close_code']})" if r.get("close_code") else ""))
    sys.exit(0 if status in PASSED else 1)


if __name__ == "__main__":
    main()
