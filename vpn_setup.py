#!/usr/bin/env python3
"""Connect the AWX server to the Cisco DevNet sandbox VPN, using only the AWX admin login.

It creates a "Sandbox VPN" credential and a "SNOW - Sandbox VPN (setup)" job template in AWX,
launches it and shows the result. Run it after run_setup.py (the project must exist).

Windows:   py vpn_setup.py
mac/Linux: python3 vpn_setup.py

Needs in .env: SANDBOX_VPN_HOST, SANDBOX_VPN_USER, SANDBOX_VPN_PASSWORD,
               AWX_HOST_SSH_USER, AWX_HOST_SSH_PASSWORD   (optional: AWX_NODE_IP, SANDBOX_ROUTER_IP)
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from run_setup import load_env  # noqa: E402

NEEDED = ["SANDBOX_VPN_HOST", "SANDBOX_VPN_USER", "SANDBOX_VPN_PASSWORD", "AWX_HOST_SSH_USER", "AWX_HOST_SSH_PASSWORD"]


def report_failure(awx, API, job):
    """Print why an AWX job failed: explanation, failed tasks with their messages, last output lines."""
    j = awx.get(f"{API}/jobs/{job}/")
    if j.get("job_explanation"):
        print(f"\nJob explanation: {j['job_explanation']}")
    if j.get("result_traceback"):
        print("Traceback (last lines):\n" + "\n".join(j["result_traceback"].strip().splitlines()[-8:]))
    try:
        ev = awx.get(f"{API}/jobs/{job}/job_events/", params={"failed": "true", "page_size": 20})
        for e in ev.get("results", []):
            res = (e.get("event_data") or {}).get("res") or {}
            msg = res.get("msg") or res.get("stderr") or res.get("module_stderr") or res.get("stdout") or ""
            if e.get("event") in ("runner_on_failed", "runner_on_unreachable") or msg:
                print(f"\nFAILED task: {e.get('task') or e.get('event_display')}  host: {e.get('host_name')}")
                print("  " + str(msg).strip()[:1500].replace("\n", "\n  "))
    except Exception as ex:  # noqa: BLE001
        print(f"(could not read job events: {ex})")
    try:
        out = awx.get(f"{API}/jobs/{job}/stdout/", params={"format": "txt"}, accept="text/plain").get("_raw", "")
        lines = [l for l in out.strip().splitlines() if "DEPRECATION" not in l]
        print("\n--- last lines of the job output ---")
        print("\n".join(lines[-25:]))
    except Exception as ex:  # noqa: BLE001
        print(f"(could not read job output: {ex})")


def main():
    load_env()
    E = os.environ
    missing = [k for k in NEEDED if not E.get(k)]
    if missing:
        sys.exit(".env is missing: " + ", ".join(missing))

    import awx_config as A  # uses AWX_URL / AWX_ADMIN_PASSWORD from .env
    from demohttp import HttpError
    awx, API = A.awx, A.API

    try:
        org_id = A.find("organizations", "Default")["id"]
        print("  - Credential type: Sandbox VPN")
        ctype = A.upsert("credential_types", "Sandbox VPN", {
            "kind": "cloud",
            "inputs": {"fields": [
                {"id": "vpn_host", "label": "VPN address (host:port)", "type": "string"},
                {"id": "vpn_user", "label": "VPN username", "type": "string"},
                {"id": "vpn_password", "label": "VPN password", "type": "string", "secret": True},
                {"id": "node_user", "label": "AWX server SSH user", "type": "string"},
                {"id": "node_password", "label": "AWX server SSH password", "type": "string", "secret": True},
                {"id": "node_ip", "label": "AWX server IP (optional)", "type": "string"},
                {"id": "router_ip", "label": "Lab router IP", "type": "string"},
            ], "required": ["vpn_host", "vpn_user", "vpn_password", "node_user", "node_password"]},
            "injectors": {"env": {
                "SANDBOX_VPN_HOST": "{{ vpn_host }}", "SANDBOX_VPN_USER": "{{ vpn_user }}",
                "SANDBOX_VPN_PASSWORD": "{{ vpn_password }}", "NODE_SSH_USER": "{{ node_user }}",
                "NODE_SSH_PASSWORD": "{{ node_password }}", "AWX_NODE_IP": "{{ node_ip }}",
                "SANDBOX_ROUTER_IP": "{{ router_ip }}"}},
        })
        print("  - Credential: Sandbox VPN")
        cred = A.upsert("credentials", "Sandbox VPN", {
            "organization": org_id, "credential_type": ctype["id"],
            "inputs": {"vpn_host": E["SANDBOX_VPN_HOST"], "vpn_user": E["SANDBOX_VPN_USER"],
                       "vpn_password": E["SANDBOX_VPN_PASSWORD"], "node_user": E["AWX_HOST_SSH_USER"],
                       "node_password": E["AWX_HOST_SSH_PASSWORD"], "node_ip": E.get("AWX_NODE_IP", ""),
                       "router_ip": E.get("SANDBOX_ROUTER_IP") or E.get("CISCO_HOST") or "10.10.20.48"},
        })
        proj = A.find("projects", "SNOW AWX Demo")
        inv = A.find("inventories", "Demo Inventory")
        if not proj or not inv:
            sys.exit("Project or inventory not found in AWX - run 'py run_setup.py' first.")
        print("  - Syncing project to pick up the VPN playbook")
        awx.post(f"{API}/projects/{proj['id']}/update/", {}, ok=(200, 201, 202, 405))
        for _ in range(60):
            time.sleep(4)
            st = awx.get(f"{API}/projects/{proj['id']}/")["status"]
            if st == "successful":
                break
            if st in ("failed", "error", "canceled"):
                sys.exit("Project sync failed - check AWX > Projects > SNOW AWX Demo.")
        print("  - Job template: SNOW - Sandbox VPN (setup)")
        jt = A.upsert("job_templates", "SNOW - Sandbox VPN (setup)", {
            "job_type": "run", "inventory": inv["id"], "project": proj["id"],
            "playbook": "playbooks/sandbox_vpn.yml", "description": "Connects the AWX server to the DevNet sandbox VPN",
        })
        attached = {c["id"] for c in awx.get(f"{API}/job_templates/{jt['id']}/credentials/")["results"]}
        if cred["id"] not in attached:
            awx.post(f"{API}/job_templates/{jt['id']}/credentials/", {"id": cred["id"]}, ok=(200, 201, 204))

        job = awx.post(f"{API}/job_templates/{jt['id']}/launch/", {})["job"]
        print(f"\n### Running AWX job #{job} (watch it live: {A.AWX_URL}/#/jobs/playbook/{job}/output)")
        status = ""
        for _ in range(120):
            time.sleep(5)
            j = awx.get(f"{API}/jobs/{job}/")
            if j["status"] != status:
                status = j["status"]
                print(f"  status: {status}")
            if status in ("successful", "failed", "error", "canceled"):
                break
        if status == "successful":
            print("\nVPN is up. Set CISCO_HOST=10.10.20.48, CISCO_USERNAME=developer, CISCO_PASSWORD=<sandbox password>"
                  " in .env and run 'py run_setup.py' again to update the Cisco credential.")
        else:
            report_failure(awx, API, job)
            sys.exit("\nVPN setup did not finish. Paste the lines above to get the fix.")
    except HttpError as e:
        sys.exit(f"AWX API error: {e}")


if __name__ == "__main__":
    main()
