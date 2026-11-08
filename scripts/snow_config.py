#!/usr/bin/env python3
"""Configure the ServiceNow PDI via Table API:
custom fields (+ choices, + form layout), system properties, async Business Rules.
Idempotent. Usage:
    snow_config.py                 full setup
    snow_config.py --url-only      only refresh the AWX URL property (after the tunnel URL changes)
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from demohttp import Client, HttpError, log  # noqa: E402

E = os.environ
ROOT = Path(__file__).resolve().parent.parent
sn = Client(f"https://{E['SN_INSTANCE']}", user=E["SN_USERNAME"], password=E["SN_PASSWORD"])
T = "/api/now/table"

STATUS_CHOICES = [("queued", "Queued"), ("running", "Running"), ("success", "Success"),
                  ("failed", "Failed"), ("rejected", "Rejected by policy"),
                  ("rolled_back", "Rolled back"), ("diagnosed", "Diagnosed")]

# (element, label, type, max_length, default, choices)
COMMON = [
    ("u_automation_status", "Automation Status", "string", 40, "", STATUS_CHOICES),
    ("u_awx_job", "AWX Job", "url", 1024, "", None),
    ("u_device", "Device", "string", 100, E.get("CISCO_INVENTORY_NAME", "demo-iosxe"), None),
    ("u_interface", "Interface", "string", 60, "", None),
]
FIELDS = {
    "change_request": [
        ("u_automation_type", "Automation Type", "string", 60, "",
         [("cisco_interface_change", "Cisco - Interface change (pre/post check)"),
          ("aws_sg_change", "AWS - Security Group rule change")]),
        *COMMON,
        ("u_int_description", "Interface Description", "string", 200, "", None),
        ("u_int_shutdown", "Shut down interface", "boolean", 40, "false", None),
        ("u_sg_id", "Security Group ID", "string", 40, E.get("DEMO_SG_ID", ""), None),
        ("u_sg_action", "SG Action", "string", 20, "add", [("add", "Add rule"), ("remove", "Remove rule")]),
        ("u_sg_protocol", "SG Protocol", "string", 10, "tcp", [("tcp", "TCP"), ("udp", "UDP")]),
        ("u_sg_port", "SG Port", "integer", 40, "", None),
        ("u_sg_cidr", "SG Source CIDR", "string", 50, "", None),
        ("u_simulate_failure", "Simulate post-check failure (demo)", "boolean", 40, "false", None),
    ],
    "incident": [
        ("u_automation_type", "Automation Type", "string", 60, "",
         [("incident_diagnostics", "Network - Auto diagnostics")]),
        *COMMON,
    ],
}


def q(table, query, fields=None, limit=1):
    params = {"sysparm_query": query, "sysparm_limit": limit}
    if fields:
        params["sysparm_fields"] = fields
    return sn.get(f"{T}/{table}", params=params)["result"]


def upsert(table, query, payload):
    found = q(table, query, "sys_id")
    if found:
        return sn.patch(f"{T}/{table}/{found[0]['sys_id']}", payload)["result"]
    return sn.post(f"{T}/{table}", payload)["result"]


def ensure_field(table, element, label, itype, maxlen, default, choices):
    if not q("sys_dictionary", f"name={table}^element={element}", "sys_id"):
        payload = {"name": table, "element": element, "column_label": label,
                   "internal_type": itype, "max_length": str(maxlen), "active": "true"}
        if default:
            payload["default_value"] = default
        if choices:
            payload["choice"] = "1"   # dropdown with -- None --
        sn.post(f"{T}/sys_dictionary", payload)
        log(f"{table}.{element} created")
    else:
        upd = {"default_value": default} if default else {}
        if upd:
            found = q("sys_dictionary", f"name={table}^element={element}", "sys_id")
            sn.patch(f"{T}/sys_dictionary/{found[0]['sys_id']}", upd)
    for seq, (val, lab) in enumerate(choices or []):
        upsert("sys_choice", f"name={table}^element={element}^value={val}^language=en",
               {"name": table, "element": element, "value": val, "label": lab,
                "sequence": str((seq + 1) * 10), "language": "en", "inactive": "false"})


def add_to_form(table, elements):
    """Append fields to the default form view (main section). Best effort."""
    try:
        sec = q("sys_ui_section", f"name={table}^view=Default view^captionISEMPTY", "sys_id")
        if not sec:
            sec = q("sys_ui_section", f"name={table}^view.name=NULL^ORview=Default view", "sys_id")
        if not sec:
            log(f"Could not locate {table} form section - add fields via Configure > Form Layout")
            return False
        sid = sec[0]["sys_id"]
        rows = sn.get(f"{T}/sys_ui_element", params={
            "sysparm_query": f"sys_ui_section={sid}^ORDERBYDESCposition",
            "sysparm_fields": "element,position", "sysparm_limit": 500})["result"]
        present = {r["element"] for r in rows}
        pos = max([int(r["position"] or 0) for r in rows] or [0]) + 1
        if ".split" not in present:
            sn.post(f"{T}/sys_ui_element", {"sys_ui_section": sid, "element": ".split",
                                             "type": ".split", "position": str(pos)})
            pos += 1
        added = 0
        for el in elements:
            if el in present:
                continue
            sn.post(f"{T}/sys_ui_element", {"sys_ui_section": sid, "element": el, "position": str(pos)})
            pos += 1
            added += 1
        log(f"{table} form: {added} fields added")
        return True
    except HttpError as e:
        log(f"Form layout update skipped ({e.code}). Add fields via Configure > Form Layout.")
        return False


def set_prop(name, value, desc):
    upsert("sys_properties", f"name={name}",
           {"name": name, "value": value, "type": "string", "description": desc})


def awx_url():
    url = E.get("AWX_PUBLIC_URL")
    if not url:
        raise SystemExit("AWX_PUBLIC_URL not set")
    return url.rstrip("/")


def main():
    if "--url-only" in sys.argv:
        set_prop("awxdemo.awx_url", awx_url(), "Public URL of AWX (cloudflared tunnel)")
        log(f"awxdemo.awx_url -> {awx_url()}")
        return

    state = json.loads((ROOT / ".state" / "awx.json").read_text())

    log("Creating custom fields")
    for table, fields in FIELDS.items():
        for f in fields:
            ensure_field(table, *f)
        add_to_form(table, [f[0] for f in fields])

    log("System properties")
    set_prop("awxdemo.awx_url", awx_url(), "Public URL of AWX (cloudflared tunnel)")
    set_prop("awxdemo.awx_token", state["token"], "AWX API token (demo)")
    set_prop("awxdemo.templates", json.dumps(state["templates"]), "Automation type -> AWX job template id")
    # lets the playbooks render HTML tables/diffs inside work notes via [code]...[/code]
    upsert("sys_properties", "name=glide.ui.security.allow_codetag",
           {"name": "glide.ui.security.allow_codetag", "value": "true", "type": "true|false"})

    script = (ROOT / "snow" / "business_rule.js").read_text()
    for table in FIELDS:
        name = f"AWX Demo - launch automation ({table})"
        log(f"Business Rule: {name}")
        upsert("sys_script", f"name={name}", {
            "name": name, "collection": table, "when": "async", "action_insert": "true",
            "action_update": "false", "active": "true", "advanced": "true", "order": "100",
            "condition": "!current.u_automation_type.nil()", "script": script,
            "description": "Launches the mapped AWX job template with ticket fields as extra_vars.",
        })
    log("ServiceNow configured")


if __name__ == "__main__":
    try:
        main()
    except HttpError as e:
        raise SystemExit(f"ServiceNow API error: {e}\n"
                         f"Tip: wake the PDI at developer.servicenow.com if it is hibernating.")
