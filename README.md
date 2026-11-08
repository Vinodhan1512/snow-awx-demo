# ServiceNow → AWX → ServiceNow: closed-loop automation demo

Three use cases. Each one **starts from a ServiceNow ticket and ends in the same ticket**:

| # | Ticket | Automation Type | What AWX does | Ends with |
|---|--------|-----------------|---------------|-----------|
| 1 | Change Request | Cisco - Interface change | Pre-check → configure interface → post-check → diff + 3 validations | Success / Failed, coloured diff in work notes, full outputs attached |
| 2 | Change Request | AWS - Security Group rule change | Policy guardrail → snapshot → change → snapshot → validate → **auto-rollback** on failure | Success / Rejected by policy / Rolled back, before/after rule tables |
| 3 | Incident | Network - Auto diagnostics | Read-only show commands → rule-based findings | Diagnosed, findings + raw outputs on the incident |

```
ServiceNow ticket ──(async Business Rule, REST)──▶ AWX job template ──▶ Cisco IOS-XE / AWS EC2 API
        ▲                                                │
        └──────── work notes + status + attachment ◀─────┘  (Table API + Attachment API)
```

---

## Setup with an AWX that is already running (about 15 minutes)

Use this path when AWX is already installed and reachable on a public HTTPS URL. Nothing is installed: the script only talks to the AWX and ServiceNow APIs, so it can run from any Linux box with `python3` and `curl`.

1. **Push this folder to GitHub** (public repo is simplest). AWX pulls the playbooks from it.
2. **Create the demo security group** (EC2 console → Security Groups → Create, same region as `AWS_REGION`): name `awx-demo-target-sg`, one inbound rule TCP 443 from `10.0.0.0/8`. Copy its ID into `DEMO_SG_ID`.
3. **Create an IAM user for AWX** (IAM → Users → Create, no console access) with this inline policy, replacing `REGION`, `ACCOUNT` and `SG_ID`, then create an access key and put it in `.env`:
   ```json
   {"Version":"2012-10-17","Statement":[
     {"Effect":"Allow","Action":["ec2:DescribeSecurityGroups","ec2:DescribeSecurityGroupRules"],"Resource":"*"},
     {"Effect":"Allow","Action":["ec2:AuthorizeSecurityGroupIngress","ec2:RevokeSecurityGroupIngress"],
      "Resource":"arn:aws:ec2:REGION:ACCOUNT:security-group/SG_ID"}]}
   ```
4. **Reserve the Cisco DevNet sandbox** and copy its username and password.
5. From **any machine with Python 3** (your Windows laptop works, no SSH needed):
   ```powershell
   cd snow-awx-demo
   copy .env.example .env      # then edit .env: AWX_ADMIN_PASSWORD, SN_*, CISCO_*, AWS_*, GIT_REPO_URL
   py run_setup.py
   ```
   On Linux/macOS use `cp` and `python3 run_setup.py`, or `./setup.sh`.
   It checks AWX is reachable, creates the credentials, project, inventory, job templates and API token in AWX, configures ServiceNow, and fires a read-only test incident.

6. **Cisco sandbox over VPN.** The on-demand Catalyst 8000v sandbox sits behind a VPN (10.10.20.0/24). Fill the `SANDBOX_VPN_*` and `AWX_HOST_SSH_*` values in `.env`, then run:
   ```powershell
   py vpn_setup.py
   ```
   It launches a one-time AWX job that SSHes from the job container to its own server, installs `openconnect`, and starts a split-tunnel VPN service (only 10.10.20.0/24 uses the tunnel, so AWX and internet traffic are unaffected). It finishes by checking the router answers on port 22. Run `py run_setup.py` again afterwards so the Cisco credential has the sandbox login. A new reservation has new VPN details: update `.env` and run `py vpn_setup.py` again.

---

## Setup on a fresh EC2 instance (about 30 minutes, mostly waiting)

### 0. Manual sign-ups (cannot be automated – card/phone/CAPTCHA checks)
1. **AWS account** – console.aws.amazon.com.
2. **ServiceNow PDI** – developer.servicenow.com → *Request Instance*. Note the instance host (`devXXXXX.service-now.com`) and the admin password.
3. **Cisco sandbox** – developer.cisco.com/sandbox → *IOS XE on Catalyst 8000 Always-On* → *Launch* (pick the longest duration). Copy the username/password from the *Quick Access* tab. They change with every reservation.
4. **GitHub repo** – create an empty repo and push this whole folder to it (public is easiest; for private, put a read-only PAT in `.env`).

### 1. Launch the EC2 instance (automated)
AWS console → **CloudShell** (terminal icon, top bar) → *Actions → Upload file* → `aws/launch-ec2.sh`, then:
```bash
bash launch-ec2.sh              # default region ap-south-1; REGION=me-central-1 bash launch-ec2.sh for UAE
```
It creates a t3.large Ubuntu 22.04 (≈ $0.09/h), an IAM role that can only change the demo SG, a host SG with **no inbound ports** (you connect through Session Manager), and the demo target SG. Note the `DEMO_SG_ID` it prints.

### 2. Run setup on the instance
EC2 → instance → **Connect → Session Manager → Connect** (wait 2–3 min after launch), then:
```bash
sudo -i
git clone https://github.com/<you>/snow-awx-demo.git && cd snow-awx-demo
cp .env.example .env && nano .env        # fill every value
./setup.sh
```
`setup.sh` installs k3s + AWX + a Cloudflare tunnel, configures AWX (credentials, project, inventory, 3 job templates, API token), configures ServiceNow (fields, form layout, properties, Business Rules), then fires a read-only smoke-test incident. It ends by printing the AWX URL.

**Before the demo (stopped instance or a new day):**
```bash
./setup.sh refresh-url       # the tunnel URL changes on every restart
./setup.sh reconfigure       # after a new Cisco sandbox reservation (update .env first)
```
Also log in to developer.servicenow.com to wake the PDI. It hibernates when idle.

---

## Live demo script (≈ 15 minutes)

Keep two browser tabs side by side: **ServiceNow** and **AWX → Jobs**.

**1. Cisco change – happy path**
- *Change → Create New → Normal*. Short description: "Add mgmt loopback for monitoring".
- Automation Type **Cisco – Interface change**, Device `demo-iosxe`, Interface `Loopback123`, Description `CHG demo – monitoring loopback`. Submit.
- Within seconds the work notes show *"Automation started – AWX job #N"* with a link. Switch to AWX and show the live job.
- About 30–60 s later, refresh the change: green **CHANGE SUCCESSFUL**, a PASS table (config present, interface up/up, no impact on other interfaces), the coloured running-config diff, and the pre/post file attached.

**2. AWS SG – blocked by policy**
- New change: Automation Type **AWS – Security Group rule change**, Port `22`, CIDR `0.0.0.0/0`.
- Result: **REJECTED BY POLICY – no change was made to AWS**, with the reasons. *Talking point: guardrails run before anything touches production.*

**3. AWS SG – compliant change**
- Port `8443`, CIDR `10.20.0.0/16` → **SUCCESSFUL**, with before/after rule tables and a diff. Optionally show the rule in the EC2 console.

**4. AWS SG – automatic rollback**
- Port `9090`, CIDR `10.30.0.0/16`, tick **Simulate post-check failure (demo)**.
- Result: amber **ROLLED BACK – security group restored**. *Talking point: failed validation never leaves a half-applied change.*

**5. Incident auto-triage**
- *Incident → Create New*: "Interface down on demo-iosxe", Automation Type **Network – Auto diagnostics**, Interface `GigabitEthernet1`.
- The incident gets findings plus command outputs before an engineer has even opened it.

**Backup if the UI misbehaves** (same result, fired from the instance):
```bash
set -a; source .env; set +a
python3 scripts/create_ticket.py cisco --interface Loopback123 --description "CHG demo"
python3 scripts/create_ticket.py sg --port 22 --cidr 0.0.0.0/0
python3 scripts/create_ticket.py sg --port 8443 --cidr 10.20.0.0/16
python3 scripts/create_ticket.py sg --port 9090 --cidr 10.30.0.0/16 --simulate-failure
python3 scripts/create_ticket.py diag
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Ticket shows *AWX launch failed: HTTP 0 / 502* | Tunnel URL changed → `./setup.sh refresh-url` |
| Status stays empty | Business Rule did not fire: check *System Definition → Business Rules → "AWX Demo"* is active and Automation Type is set |
| Fields not on the form | *Configure → Form Layout* and add the `u_` fields (the API layout step is best-effort) |
| Work notes show raw `<b>` tags | Set system property `glide.ui.security.allow_codetag` = true |
| Cisco job: authentication failed | Sandbox reservation expired → re-launch, update `.env`, `./setup.sh reconfigure` |
| Cisco job: `paramiko`/`pylibssh` not found | The default AWX EE lacks the SSH library. Build a custom EE (ansible-builder with `ansible-pylibssh`) and set it on the job templates |
| SG job: `NoCredentialsError` | The instance needs IMDS hop limit 2 (set by launch-ec2.sh). Check: `aws ec2 describe-instances --query 'Reservations[].Instances[].MetadataOptions'` |
| Project sync failed | AWX → Projects → *SNOW AWX Demo* → last job log (wrong repo URL, private repo without token) |
| AWX pods not starting | `kubectl -n awx get pods`, `kubectl -n awx logs deploy/awx-operator-controller-manager` |

## Cost and cleanup
- t3.large ≈ $0.09/hour in ap-south-1. **Stop the instance** when you are not using it.
- Remove everything: run `aws/destroy.sh` in CloudShell.
- The PDI is free. The DevNet reservation expires on its own.

## Layout
```
aws/                 launch-ec2.sh, destroy.sh       (run in CloudShell)
setup.sh             one-shot installer/configurator (run on EC2)
scripts/             install_awx.sh, awx_config.py, snow_config.py, create_ticket.py
snow/                business_rule.js                (pushed to ServiceNow by snow_config.py)
playbooks/           cisco_interface_change.yml, aws_sg_change.yml, incident_diagnostics.yml
  tasks/             snow_update.yml, snow_attach.yml (write-back to the same ticket)
  library/sg_rule.py single-rule SG module (boto3)
  filter_plugins/    diff, HTML report, policy and findings filters
  vars/sg_policy.yml guardrails – edit to tell your client's story
```

Demo-grade shortcuts to call out to the client: the PDI admin account is used for write-back, the AWX token is stored as a plain system property, and the trigger fires on insert. In production these become a MID Server or OAuth integration user, a credential alias, and a trigger on the *Implement* state with CAB approval.
