#!/usr/bin/env python3
"""Configure AWX via its REST API: credentials, project, inventory, job templates, API token.
Idempotent. Writes results to .state/awx.json for snow_config.py."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from demohttp import Client, HttpError, log  # noqa: E402

E = os.environ
ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".state"
STATE.mkdir(exist_ok=True)

AWX_URL = (E.get("AWX_URL") or "http://localhost:30080").rstrip("/")
AWX_VERIFY = E.get("AWX_VERIFY_TLS", "true").lower() != "false"
awx = Client(AWX_URL, user=E.get("AWX_USERNAME") or "admin", password=E["AWX_ADMIN_PASSWORD"], verify=AWX_VERIFY)
API = "/api/v2"

TEMPLATES = {
    # automation_type (ServiceNow value) : (job template name, playbook, needs cisco cred, default extra vars)
    "cisco_interface_change": ("SNOW - Cisco Interface Change", "playbooks/cisco_interface_change.yml", True, {}),
    "aws_sg_change": ("SNOW - AWS Security Group Change", "playbooks/aws_sg_change.yml", False,
                      {"aws_region": E.get("AWS_REGION", "ap-south-1")}),
    "incident_diagnostics": ("SNOW - Incident Auto-Diagnostics", "playbooks/incident_diagnostics.yml", True, {}),
}


def find(endpoint, name):
    res = awx.get(f"{API}/{endpoint}/", params={"name": name})
    return res["results"][0] if res.get("count") else None


def upsert(endpoint, name, payload):
    payload = dict(payload, name=name)
    obj = find(endpoint, name)
    if obj:
        return awx.patch(f"{API}/{endpoint}/{obj['id']}/", payload)
    return awx.post(f"{API}/{endpoint}/", payload)


def cred_type_id(name, kind=None):
    params = {"name": name}
    if kind:
        params["kind"] = kind
    res = awx.get(f"{API}/credential_types/", params=params)
    if not res["count"]:
        raise SystemExit(f"Credential type '{name}' not found in AWX")
    return res["results"][0]["id"]


def main():
    org = find("organizations", "Default")
    org_id = org["id"]

    # ---------- credential types ----------
    log("Credential type: ServiceNow Demo")
    sn_type = upsert("credential_types", "ServiceNow Demo", {
        "kind": "cloud",
        "inputs": {
            "fields": [
                {"id": "instance", "label": "Instance host (dev12345.service-now.com)", "type": "string"},
                {"id": "username", "label": "Username", "type": "string"},
                {"id": "password", "label": "Password", "type": "string", "secret": True},
            ],
            "required": ["instance", "username", "password"],
        },
        "injectors": {"env": {
            "SN_INSTANCE": "{{ instance }}", "SN_USERNAME": "{{ username }}", "SN_PASSWORD": "{{ password }}"}},
    })

    # ---------- credentials ----------
    log("Credential: ServiceNow PDI")
    sn_cred = upsert("credentials", "ServiceNow PDI", {
        "organization": org_id, "credential_type": sn_type["id"],
        "inputs": {"instance": E["SN_INSTANCE"], "username": E["SN_USERNAME"], "password": E["SN_PASSWORD"]},
    })
    log("Credential: Cisco Device")
    cisco_cred = upsert("credentials", "Cisco Device", {
        "organization": org_id, "credential_type": cred_type_id("Machine", "ssh"),
        "inputs": {"username": E["CISCO_USERNAME"], "password": E["CISCO_PASSWORD"]},
    })
    scm_cred_id = None
    if E.get("GIT_TOKEN"):
        log("Credential: Git Token")
        scm = upsert("credentials", "Git Token", {
            "organization": org_id, "credential_type": cred_type_id("Source Control", "scm"),
            "inputs": {"username": E.get("GIT_USERNAME") or "git", "password": E["GIT_TOKEN"]},
        })
        scm_cred_id = scm["id"]

    aws_cred_id = None
    if E.get("AWS_ACCESS_KEY_ID") and E.get("AWS_SECRET_ACCESS_KEY"):
        log("Credential: AWS Demo (access keys)")
        aws = upsert("credentials", "AWS Demo", {
            "organization": org_id, "credential_type": cred_type_id("Amazon Web Services", "cloud"),
            "inputs": {"username": E["AWS_ACCESS_KEY_ID"], "password": E["AWS_SECRET_ACCESS_KEY"]},
        })
        aws_cred_id = aws["id"]
    else:
        log("No AWS keys in .env - SG playbook will use the AWX host's IAM role")

    # ---------- project ----------
    log(f"Project from {E['GIT_REPO_URL']}")
    proj = upsert("projects", "SNOW AWX Demo", {
        "organization": org_id, "scm_type": "git", "scm_url": E["GIT_REPO_URL"],
        "scm_branch": E.get("GIT_BRANCH", "main"), "credential": scm_cred_id,
        "scm_update_on_launch": True, "scm_update_cache_timeout": 0,
    })
    awx.post(f"{API}/projects/{proj['id']}/update/", {}, ok=(200, 201, 202, 405))
    log("Waiting for project sync (installs cisco.ios / netcommon collections)")
    for _ in range(90):
        time.sleep(5)
        p = awx.get(f"{API}/projects/{proj['id']}/")
        if p["status"] in ("successful",):
            break
        if p["status"] in ("failed", "error", "canceled"):
            raise SystemExit(f"Project sync {p['status']}. Open AWX > Projects > SNOW AWX Demo > Jobs for the log.")
    else:
        raise SystemExit("Project sync timed out")
    playbooks = awx.get(f"{API}/projects/{proj['id']}/playbooks/")
    for _, (_, pb, _, _) in TEMPLATES.items():
        if pb not in playbooks:
            raise SystemExit(f"Playbook {pb} not found in repo. Did you push the whole bundle? Found: {playbooks}")

    # ---------- inventory ----------
    log("Inventory: Demo Inventory")
    inv = upsert("inventories", "Demo Inventory", {"organization": org_id})
    hosts = {
        E.get("CISCO_INVENTORY_NAME", "demo-iosxe"): {
            "ansible_host": E["CISCO_HOST"],
            "ansible_network_os": "cisco.ios.ios",
            "ansible_connection": "ansible.netcommon.network_cli",
            "ansible_host_key_checking": False,
            "ansible_paramiko_host_key_checking": False,
        },
        "localhost": {
            "ansible_connection": "local",
            "ansible_python_interpreter": "{{ ansible_playbook_python }}",
        },
    }
    for hname, hvars in hosts.items():
        existing = awx.get(f"{API}/inventories/{inv['id']}/hosts/", params={"name": hname})
        payload = {"name": hname, "inventory": inv["id"], "variables": json.dumps(hvars)}
        if existing["count"]:
            awx.patch(f"{API}/hosts/{existing['results'][0]['id']}/", payload)
        else:
            awx.post(f"{API}/hosts/", payload)

    # ---------- job templates ----------
    template_ids = {}
    for atype, (jt_name, playbook, needs_cisco, defaults) in TEMPLATES.items():
        log(f"Job template: {jt_name}")
        jt = upsert("job_templates", jt_name, {
            "job_type": "run", "inventory": inv["id"], "project": proj["id"], "playbook": playbook,
            "ask_variables_on_launch": True, "extra_vars": json.dumps(defaults),
            "verbosity": 0, "description": f"Triggered by ServiceNow ({atype})",
        })
        creds = [sn_cred["id"]] + ([cisco_cred["id"]] if needs_cisco else [])
        if atype == "aws_sg_change" and aws_cred_id:
            creds.append(aws_cred_id)
        attached = {c["id"] for c in awx.get(f"{API}/job_templates/{jt['id']}/credentials/")["results"]}
        for cid in creds:
            if cid not in attached:
                awx.post(f"{API}/job_templates/{jt['id']}/credentials/", {"id": cid}, ok=(200, 201, 204))
        template_ids[atype] = jt["id"]

    # ---------- API token for ServiceNow ----------
    state_file = STATE / "awx.json"
    old = json.loads(state_file.read_text()) if state_file.exists() else {}
    token = old.get("token")
    if token:
        try:
            Client(AWX_URL, token=token, verify=AWX_VERIFY).get(f"{API}/me/")
        except HttpError:
            token = None
    if not token:
        log("Creating AWX API token for ServiceNow")
        me = awx.get(f"{API}/me/")["results"][0]
        tok = awx.post(f"{API}/users/{me['id']}/personal_tokens/",
                       {"description": "ServiceNow demo trigger", "scope": "write"})
        token = tok["token"]

    state = {"templates": template_ids, "token": token}
    state_file.write_text(json.dumps(state, indent=2))
    os.chmod(state_file, 0o600)
    log(f"AWX ready. Templates: {template_ids}")


if __name__ == "__main__":
    try:
        main()
    except HttpError as e:
        raise SystemExit(f"AWX API error: {e}")
