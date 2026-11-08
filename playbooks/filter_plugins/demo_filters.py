"""Filters used by the demo playbooks (stdlib only)."""
import difflib
import html
import ipaddress
import re


def _truthy(v):
    return str(v).strip().lower() in ("true", "1", "yes", "on")


def unified_diff(before, after, fromfile="pre-check", tofile="post-check"):
    a = (before or "").splitlines()
    b = (after or "").splitlines()
    return "\n".join(difflib.unified_diff(a, b, fromfile=fromfile, tofile=tofile, lineterm="", n=1))


def diff_html(diff_text):
    """Render a unified diff as coloured HTML for ServiceNow work notes."""
    if not diff_text.strip():
        return '<i>No differences</i>'
    out = []
    for line in diff_text.splitlines():
        esc = html.escape(line)
        if line.startswith("+") and not line.startswith("+++"):
            out.append(f'<span style="background:#e6ffec;color:#116329">{esc}</span>')
        elif line.startswith("-") and not line.startswith("---"):
            out.append(f'<span style="background:#ffebe9;color:#82071e">{esc}</span>')
        elif line.startswith("@@"):
            out.append(f'<span style="color:#6e7781">{esc}</span>')
        else:
            out.append(esc)
    return '<pre style="font-size:12px;line-height:1.35">' + "\n".join(out) + "</pre>"


def checks_html(checks, title="Validation"):
    """checks: list of {name, ok, detail}"""
    rows = []
    for c in checks:
        if isinstance(c, str):
            c = {"name": "Policy", "ok": False, "detail": c}
        badge = ('<b style="color:#116329">PASS</b>' if _truthy(c.get("ok"))
                 else '<b style="color:#cf222e">FAIL</b>')
        rows.append(f"<tr><td>{badge}</td><td>{html.escape(str(c.get('name', '')))}</td>"
                    f"<td>{html.escape(str(c.get('detail', '')))}</td></tr>")
    return (f"<b>{html.escape(title)}</b>"
            '<table border="1" cellpadding="4" style="border-collapse:collapse;font-size:12px">'
            "<tr><th>Result</th><th>Check</th><th>Detail</th></tr>" + "".join(rows) + "</table>")


def rules_html(rules, title):
    rows = "".join(f"<tr><td>{html.escape(r['protocol'])}</td><td>{html.escape(str(r['port']))}</td>"
                   f"<td>{html.escape(r['source'])}</td><td>{html.escape(r.get('description') or '')}</td></tr>"
                   for r in rules) or '<tr><td colspan="4"><i>no inbound rules</i></td></tr>'
    return (f"<b>{html.escape(title)}</b>"
            '<table border="1" cellpadding="4" style="border-collapse:collapse;font-size:12px">'
            "<tr><th>Proto</th><th>Port</th><th>Source</th><th>Description</th></tr>" + rows + "</table>")


def rules_list(rules):
    return [f"{r['protocol']}/{r['port']} from {r['source']}" for r in rules]


def rules_text(rules):
    return "\n".join(f"{r['protocol']}/{r['port']} from {r['source']}" for r in rules)


def intf_line(ip_int_brief, intf):
    """Return the 'show ip interface brief' line for an interface ('' if absent)."""
    for line in (ip_int_brief or "").splitlines():
        parts = line.split()
        if parts and parts[0].lower() == intf.lower():
            return line.strip()
    return ""


def other_intfs(ip_int_brief, intf):
    """'show ip interface brief' without the header and the target interface (blast-radius check)."""
    keep = []
    for line in (ip_int_brief or "").splitlines():
        parts = line.split()
        if not parts or parts[0] == "Interface" or parts[0].lower() == intf.lower():
            continue
        keep.append(" ".join(parts))
    return "\n".join(keep)


def sg_policy_violations(req, policy):
    """Return a list of human-readable policy violations for an SG change request."""
    v = []
    proto = str(req.get("protocol", "")).lower()
    if proto not in policy.get("allowed_protocols", ["tcp", "udp"]):
        v.append(f"Protocol '{proto}' is not allowed")
    try:
        port = int(req.get("port"))
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        return v + [f"Port '{req.get('port')}' is not a valid port number"]
    try:
        net = ipaddress.ip_network(str(req.get("cidr", "")).strip(), strict=True)
    except ValueError as e:
        return v + [f"Source CIDR '{req.get('cidr')}' is invalid ({e})"]
    if req.get("action") == "remove":
        return v  # removals only need valid input
    world = net.prefixlen == 0
    if world and port in policy.get("sensitive_ports", []):
        v.append(f"Port {port} must never be open to the internet ({net})")
    if world and not policy.get("allow_world_open", False):
        v.append(f"Internet-wide source {net} is not allowed - use a specific range")
    min_prefix = policy.get("min_prefix_v4", 8) if net.version == 4 else policy.get("min_prefix_v6", 32)
    if 0 < net.prefixlen < min_prefix:
        v.append(f"Source range {net} is broader than /{min_prefix}")
    return v


def intf_findings(show_intf, intf):
    """Very small rule engine over 'show interfaces <intf>' output."""
    t = show_intf or ""
    f = []
    m = re.search(r"is (administratively down|up|down), line protocol is (up|down)", t)
    if not m:
        return [{"severity": "high", "text": f"Interface {intf} not found or output unreadable"}], "unknown"
    admin, proto = m.group(1), m.group(2)
    if admin == "administratively down":
        f.append({"severity": "high", "text": f"{intf} is administratively shut down - likely a config change. "
                                              "Recommended: raise a change to 'no shutdown'."})
        state = "admin-down"
    elif admin == "down":
        f.append({"severity": "high", "text": f"{intf} is down at layer 1 - check cabling/optic/far end."})
        state = "down"
    elif proto == "down":
        f.append({"severity": "high", "text": f"{intf} up but line protocol down - encapsulation/keepalive mismatch."})
        state = "proto-down"
    else:
        f.append({"severity": "info", "text": f"{intf} is up/up - fault may be intermittent or already cleared."})
        state = "up"
    for label, rx in (("input errors", r"(\d+) input errors"), ("CRC errors", r"(\d+) CRC"),
                      ("output errors", r"(\d+) output errors"), ("interface resets", r"(\d+) interface resets")):
        mm = re.search(rx, t)
        if mm and int(mm.group(1)) > 0:
            f.append({"severity": "medium", "text": f"{mm.group(1)} {label} counted on {intf}."})
    mm = re.search(r"Last input ([^,]+), output ([^,]+),", t)
    if mm:
        f.append({"severity": "info", "text": f"Last input {mm.group(1)}, last output {mm.group(2)}."})
    return f, state


def diag_pairs(results):
    """Loop results of ios_command -> [{cmd, out}], tolerating failed commands."""
    pairs = []
    for r in results or []:
        out = r.get("stdout")
        if isinstance(out, list):
            out = out[0] if out else ""
        if r.get("failed"):
            out = f"(command failed: {r.get('msg', 'unknown error')})"
        pairs.append({"cmd": r.get("item", "?"), "out": out or ""})
    return pairs


class FilterModule:
    def filters(self):
        return {
            "unified_diff": unified_diff, "diff_html": diff_html, "checks_html": checks_html,
            "rules_html": rules_html, "rules_text": rules_text, "rules_list": rules_list, "intf_line": intf_line,
            "other_intfs": other_intfs, "sg_policy_violations": sg_policy_violations,
            "intf_findings": intf_findings, "diag_pairs": diag_pairs,
        }
