# Handover: ServiceNow → AWX → ServiceNow closed-loop demo

Status: **working end to end** and tested on a ServiceNow PDI (Brazil release), AWX 24.6.1 on k3s (EC2, Amazon Linux),
a Cisco DevNet *Catalyst 8000v* sandbox and an AWS security group.
For the live demo run-sheet see [README.md](README.md). This file explains how the pieces fit, how to operate
and recover them, and what is deliberately demo-grade.

> Secrets and environment-specific values (instance name, AWX URL, passwords, keys) live only in `.env`,
> which is git-ignored. Copy `.env.example` and fill it in. Never commit `.env`, `*.pem` or `.state/`.

---

## 1. What the demo does

A ticket raised in ServiceNow (web form, API or Virtual Agent chat) triggers an AWX job. The playbook does the
work and writes the result back to **the same ticket** (status, HTML report in the work notes, evidence file),
and ServiceNow then closes or resolves the ticket.

| Automation Type (ticket field) | Ticket | AWX job template | What it does |
|---|---|---|---|
| Network - Auto diagnostics | Incident | SNOW - Incident Auto-Diagnostics | Read-only show commands on the router → findings |
| Cisco - Interface change (pre/post check) | Change | SNOW - Cisco Interface Change | Guardrail → pre-check → configure 1..20 interfaces → post-check → diff |
| AWS - Security Group rule change | Change | SNOW - AWS Security Group Change | Policy check → snapshot → change → validate → auto-rollback on failure |

```
 ServiceNow ticket (Automation Type set, on insert)
   │  Business Rule "AWX Demo - launch (<table>)"  ── REST POST /api/v2/job_templates/<id>/launch/
   ▼
 AWX job template  ── Ansible playbook (pulled from this GitHub repo on every launch)
   │                   └── Cisco IOS-XE over the sandbox VPN  /  AWS EC2 API
   ▼
 Table API PATCH: work_notes + u_automation_status   +   Attachment API: evidence file
   │
   ▼
 Business Rule "AWX Demo - close (<table>)"  ── closes / resolves / cancels the ticket
```

## 2. Components and where they live

| Piece | Where | Created by |
|---|---|---|
| Custom fields `u_automation_type`, `u_automation_status`, `u_awx_job`, `u_device`, `u_interface`, `u_int_*`, `u_sg_*`, `u_simulate_failure` | ServiceNow `incident`, `change_request` (Default view) | `scripts/snow_config.py` |
| System properties `awxdemo.awx_url`, `awxdemo.awx_token`, `awxdemo.templates` | ServiceNow | `snow_config.py` |
| Business Rules `AWX Demo - launch (incident / change_request)` | ServiceNow, after insert | `snow/business_rule.js` |
| Business Rules `AWX Demo - close (incident / change_request)` | ServiceNow, after update | `snow/close_rule.js` |
| Script Include `AWXDemoAgent` (chat backend) | ServiceNow | `snow/agent_script_include.js` |
| Virtual Agent topics `AWX Demo - Diagnose interface / Change loopback / Automation status` | ServiceNow (Designer) | `scripts/va_topics.py` |
| Credentials, project, inventory, 3 job templates, API token | AWX | `scripts/awx_config.py` (token kept in `.state/awx.json`) |
| Playbooks, filters, guardrail policy | this repo, `playbooks/` | — |
| Sandbox VPN on the AWX server (split tunnel, only 10.10.20.0/24) | EC2 host: container `sandbox-vpn` | `scripts/sandbox_vpn_host.sh` (or `vpn_setup.py`) |

Everything is created idempotently: re-running setup updates records by name and never duplicates them.

## 3. Day-to-day operation

| Task | Command (Windows: `py`, Linux/macOS: `python3`) |
|---|---|
| Full (re)configuration of AWX + ServiceNow + smoke test | `py run_setup.py` |
| ServiceNow side only | `py scripts/snow_config.py` (after `load_env`, or via `run_setup.py`) |
| Fire a demo ticket and follow it | `py scripts/create_ticket.py diag \| cisco \| sg [options]` |
| Explain why a ticket did not round-trip (read-only) | `py scripts/diagnose.py [INC…/CHG…]` |
| Reconnect the AWX server to a new sandbox VPN | `py vpn_setup.py` (needs `AWX_HOST_SSH_KEY_FILE`), or run `scripts/sandbox_vpn_host.sh` on the server |

Examples:
```powershell
py scripts/create_ticket.py diag                                   # incident diagnostics
py scripts/create_ticket.py cisco --interface Loopback200-209      # bulk: 10 loopbacks in one change
py scripts/create_ticket.py sg --port 22 --cidr 0.0.0.0/0          # rejected by policy
py scripts/create_ticket.py sg --port 8443 --cidr 10.20.0.0/16     # compliant change
py scripts/create_ticket.py sg --port 9090 --cidr 10.30.0.0/16 --simulate-failure   # rollback
```
`create_ticket.py` prints each status change and the final ticket state (Closed / Resolved / Canceled / open).

### Before a demo
1. Log in at developer.servicenow.com to wake the PDI (it hibernates when idle).
2. Make sure the Cisco sandbox reservation is active. **A new reservation changes the VPN port and passwords** (section 4).
3. `py scripts/create_ticket.py diag` must end with `diagnosed` and `ticket state: Resolved`.
4. Use fresh interface numbers or SG ports so the client sees a real diff.

## 4. Runbooks

**New Cisco sandbox reservation** (symptom: Cisco jobs fail with `timed out` at PRE-CHECK)
1. Update `.env` from the sandbox *Access Details*: `SANDBOX_VPN_HOST` (with the new `:port`), `SANDBOX_VPN_USER`,
   `SANDBOX_VPN_PASSWORD`, `CISCO_PASSWORD` (usually `CISCO_HOST=10.10.20.48`, `CISCO_USERNAME=developer`). Save the file.
2. Reconnect the VPN on the AWX server, either:
   - `py vpn_setup.py`. It needs the EC2 key: put the `.pem` in the repo folder and set `AWX_HOST_SSH_KEY_FILE`.
     The server rejects password SSH. Or:
   - AWS console → EC2 → instance → Connect → EC2 Instance Connect, then
     `curl -fsSL https://raw.githubusercontent.com/Vinodhan1512/snow-awx-demo/main/scripts/sandbox_vpn_host.sh -o vpn.sh && sudo bash vpn.sh`
     and answer the three prompts. Wait for `VPN is up: 10.10.20.48:22 is reachable`.
3. `py run_setup.py` updates the Cisco credential in AWX and runs the smoke test.

**Ticket status stays empty**: run `py scripts/diagnose.py`. No `[AWX Demo] rule started` line in the log means the
launch rule did not run (see section 6, the reserved-word issue), Automation Type was not set on insert, or the
PDI was asleep. `AWX launch failed: HTTP …` in the work notes means ServiceNow could not reach AWX or the token is invalid.

**Summary not visible on a change**: it is there. The change form is tabbed: the report is in the **Notes** tab
(Activity), close notes in **Closure Information**, the evidence file under the paperclip. Reload the form after
the job finishes.

## 5. Behaviour reference

**Bulk interfaces**: *Interface* accepts `Loopback200`, a range `Loopback200-209` or a list `Lo200,Lo201,Lo305`
(max 20, field length 255). Each interface gets its own post-check rows. If any one of them is protected
(`GigabitEthernet1`, the sandbox management link) the whole change is refused before anything is configured.

**Automatic closing** (`snow/close_rule.js`):

| Final `u_automation_status` | Change request | Incident |
|---|---|---|
| success / diagnosed | Closed – Successful | Resolved – Fixed Without Action |
| rolled_back | Closed – Unsuccessful | — |
| rejected | Canceled | — |
| action_required (high-severity finding) | — | In Progress, left for an engineer |
| failed | stays open | stays open |

**AWS guardrails** (`playbooks/vars/sg_policy.yml`): TCP/UDP only; no `0.0.0.0/0`; sensitive ports
(22, 23, 3389, 1433, 3306, 5432, 6379, 9200, 27017) never wide open; nothing broader than /8.
The AWX IAM user can change only the demo security group.

**Virtual Agent chat** (needs the plugin *Glide Virtual Agent* and the role `virtual_agent_admin` for the setup user):
type `awx diagnose`, `awx loopback` or `awx status`. The bot creates the ticket through `AWXDemoAgent`, so the
normal Business Rule → AWX chain runs. Topics must be **published** once in Virtual Agent Designer after setup;
publish without an NLU model. No LLM is involved: topic discovery is ServiceNow NLU with a keyword fallback, which
is why the distinctive `awx …` keywords exist.

## 6. Issues found and fixed (worth knowing)

| Symptom | Root cause | Fix |
|---|---|---|
| Launch rule never ran, nothing in syslog (async and after) | Unquoted `interface:` object key. It is a reserved word in ServiceNow's Rhino engine, so the script fails to compile and ServiceNow **silently** skips the rule | Keys quoted; `snow_config.py` now refuses to push a script with reserved-word keys (`check_rhino_safe`) |
| Diagnostics job failed writing work notes (`no attribute 'severity'`) | A filter returned a Python tuple, which Ansible turns into a string | Filter returns a dict |
| Invalid choice values silently blanked | ServiceNow drops values that are not in the field's choice list | Use the documented choice values |
| Cisco jobs `timed out` | Sandbox reservation ended; the AWX server's VPN still pointed at the old one | Runbook in section 4 |
| Custom VA topics could not be created (HTTP 403) | Only *Virtual Agent Lite* was active; topic records were writable by the internal `maint` role only | Activate *Glide Virtual Agent* and grant `virtual_agent_admin`; setup skips topics with a hint otherwise |

## 7. Demo-grade shortcuts (call these out to a client)

- The PDI **admin** account does the write-back, and the AWX token is a plain system property. Production: an
  integration user with OAuth, and a credential alias / connection record.
- The launch rule fires **on insert** and calls AWX synchronously (about 1–2 s). Production: trigger on the
  *Implement* state after CAB approval, through IntegrationHub or a MID Server.
- Changes are closed with the change **state engine bypassed** (`setWorkflow(false)`), because a Normal change
  cannot go from New to Closed. Production: walk the change model (Assess → Authorize → Implement → Review → Closed).
- The Cisco sandbox is reached over a split-tunnel VPN on the AWX host, and the router credentials are sandbox defaults.
- AWX runs with a self-managed k3s install on a single EC2 instance. Stop the instance when it is not needed
  (`aws/destroy.sh` removes everything).

## 8. Last verified

All use cases passed against the live environment: diagnostics (Resolved), diagnostics with a real fault
(action required, left open), single and bulk (10 interfaces) Cisco changes (Closed – Successful), protected
interface refused (left open), SG rejected (Canceled), SG compliant (Closed – Successful), SG rollback
(Closed – Unsuccessful), a ticket raised from the web form, and the Virtual Agent backend.
Re-verify after a new sandbox reservation with the commands in section 3.
