#!/usr/bin/env python3
"""Explain why a ticket did not round-trip. Read-only.

    py scripts/diagnose.py              (checks the most recent demo ticket)
    py scripts/diagnose.py INC0010003   (checks a specific ticket)
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from run_setup import load_env  # noqa: E402
from demohttp import Client, HttpError  # noqa: E402

load_env()
E = os.environ
sn = Client(f"https://{E['SN_INSTANCE']}", user=E["SN_USERNAME"], password=E["SN_PASSWORD"])
awx = Client(E["AWX_URL"], user=E.get("AWX_USERNAME") or "admin", password=E["AWX_ADMIN_PASSWORD"],
             verify=E.get("AWX_VERIFY_TLS", "true").lower() != "false")
T = "/api/now/table"


def q(table, query, fields, limit=10):
    try:
        return sn.get(f"{T}/{table}", params={"sysparm_query": query, "sysparm_fields": fields,
                                              "sysparm_limit": limit, "sysparm_display_value": "true"})["result"]
    except HttpError as e:
        return [{"error": f"HTTP {e.code}: {e.body[:200]}"}]


def head(t):
    print(f"\n=== {t} ===")


# ---- 1. the ticket ----
num = sys.argv[1] if len(sys.argv) > 1 else None
rec, table = None, None
for tb in ("incident", "change_request"):
    query = f"number={num}" if num else "u_automation_typeISNOTEMPTY^ORDERBYDESCsys_created_on"
    r = q(tb, query, "sys_id,number,sys_created_on,u_automation_type,u_automation_status,u_awx_job,u_device,u_interface", 1)
    if r and "error" not in r[0] and (not rec or r[0]["sys_created_on"] > rec["sys_created_on"]):
        rec, table = r[0], tb
head("Ticket")
if not rec:
    print("No ticket with an Automation Type found. Are the custom fields on the table?")
else:
    print(json.dumps(rec, indent=2))
    head("Work notes on the ticket")
    raw = sn.get(f"{T}/{table}/{rec['sys_id']}", params={"sysparm_fields": "sys_id"})  # resolve real sys_id
    sid = raw["result"]["sys_id"]
    notes = q("sys_journal_field", f"element_id={sid}^element=work_notes^ORDERBYsys_created_on", "sys_created_on,value", 20)
    if not notes:
        print("(none) -> the Business Rule never wrote to this ticket")
    for n in notes:
        print(f"- {n.get('sys_created_on')}: {str(n.get('value'))[:300]}")

# ---- 2. business rules ----
head("Business Rules")
for b in q("sys_script", "nameSTARTSWITHAWX Demo", "name,collection,active,when,action_insert,condition,sys_scope"):
    print(json.dumps(b))

# ---- 3. properties ----
head("System properties")
for p in q("sys_properties", "nameSTARTSWITHawxdemo.", "name,value"):
    v = p.get("value", "")
    print(f"- {p.get('name')} = {v[:6] + '...(hidden)' if p.get('name') == 'awxdemo.awx_token' else v}")

# ---- 4. recent ServiceNow errors ----
head("Recent ServiceNow errors/warnings (last 60 min)")
logs = q("syslog", "sys_created_on>javascript:gs.minutesAgoStart(60)^level=2^ORlevel=1^ORDERBYDESCsys_created_on",
         "sys_created_on,level,source,message", 15)
if not logs:
    print("(none)")
for l in logs:
    print(f"- {l.get('sys_created_on')} [{l.get('source')}] {str(l.get('message'))[:250]}")

# ---- 5. AWX jobs ----
head("Recent AWX jobs")
try:
    jobs = awx.get("/api/v2/jobs/", params={"order_by": "-id", "page_size": 6})
    for j in jobs.get("results", []):
        print(f"- #{j['id']} {j['name']}: {j['status']} ({j['created'][:19]}) launched_by={j.get('launched_by', {}).get('name')}")
except Exception as e:  # noqa: BLE001
    print(f"(could not read AWX jobs: {e})")
