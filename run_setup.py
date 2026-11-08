#!/usr/bin/env python3
"""Configure an EXISTING AWX + the ServiceNow PDI from any machine (Windows, macOS, Linux).
Needs only Python 3.8+ (standard library) and internet access to AWX and ServiceNow. No SSH needed.

Windows:   py run_setup.py            (or: python run_setup.py)
mac/Linux: python3 run_setup.py

Options:  --skip-test   do not fire the read-only smoke-test incident
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
from demohttp import Client, HttpError  # noqa: E402

REQUIRED = ["SN_INSTANCE", "SN_USERNAME", "SN_PASSWORD", "GIT_REPO_URL", "AWX_URL", "AWX_ADMIN_PASSWORD",
            "CISCO_HOST", "CISCO_USERNAME", "CISCO_PASSWORD"]


def say(msg):
    print(f"\n### {msg}", flush=True)


def load_env():
    env_file = ROOT / ".env"
    if not env_file.exists():
        sys.exit("Missing .env - copy .env.example to .env and fill it in.")
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip()
        if val.startswith("#"):
            val = ""
        elif val[:1] in "\"'" and val[-1:] == val[:1] and len(val) > 1:
            val = val[1:-1]
        elif " #" in val or "\t#" in val:
            val = val.split("#", 1)[0].strip()
        os.environ[key.strip()] = val
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        sys.exit(".env is missing values for: " + ", ".join(missing))
    sn = os.environ["SN_INSTANCE"].replace("https://", "").replace("http://", "").strip("/")
    os.environ["SN_INSTANCE"] = sn
    os.environ["AWX_URL"] = os.environ["AWX_URL"].rstrip("/")
    os.environ["AWX_PUBLIC_URL"] = os.environ["AWX_URL"]


def run(script, *args):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], cwd=ROOT, env=os.environ)
    return r.returncode


def main():
    load_env()
    E = os.environ
    verify = E.get("AWX_VERIFY_TLS", "true").lower() != "false"

    say(f"Checking AWX at {E['AWX_URL']}")
    try:
        ping = Client(E["AWX_URL"], verify=verify).get("/api/v2/ping/")
        print(f"  - AWX {ping.get('version', '?')} is reachable")
        Client(E["AWX_URL"], user=E.get("AWX_USERNAME") or "admin",
               password=E["AWX_ADMIN_PASSWORD"], verify=verify).get("/api/v2/me/")
        print("  - Login OK")
    except HttpError as e:
        sys.exit(f"AWX login failed: {e}")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Cannot reach AWX: {e}\nIf it is a certificate error, set AWX_VERIFY_TLS=false in .env")

    say(f"Checking ServiceNow at {E['SN_INSTANCE']}")
    try:
        Client(f"https://{E['SN_INSTANCE']}", user=E["SN_USERNAME"], password=E["SN_PASSWORD"]) \
            .get("/api/now/table/sys_user", params={"sysparm_limit": 1, "sysparm_fields": "sys_id"})
        print("  - Login OK")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"ServiceNow check failed: {e}\nWake the PDI at developer.servicenow.com and check the admin password.")

    say("1/3  Configuring AWX")
    if run("awx_config.py"):
        sys.exit("AWX configuration failed - see the message above.")

    say("2/3  Configuring ServiceNow")
    if run("snow_config.py"):
        sys.exit("ServiceNow configuration failed - see the message above.")

    if "--skip-test" not in sys.argv and E.get("RUN_SMOKE_TEST", "true").lower() == "true":
        say("3/3  Smoke test: read-only diagnostics incident")
        if run("create_ticket.py", "diag"):
            print("Smoke test did not pass. Open the incident's work notes and the AWX job output, "
                  "or see README > Troubleshooting.")

    print(f"""
=====================================================================
 READY FOR THE DEMO
 AWX ........... {E['AWX_URL']}
 ServiceNow .... https://{E['SN_INSTANCE']}
 Run-sheet ..... README.md > "Live demo script"
 Backup tickets: py scripts/create_ticket.py cisco | sg | diag
=====================================================================""")


if __name__ == "__main__":
    main()
