#!/usr/bin/python
"""Minimal, deterministic security-group ingress rule module (boto3 only).
action=describe | add | remove. Touches only the single rule requested."""
from ansible.module_utils.basic import AnsibleModule

try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False


def normalize(rules):
    out = []
    for r in rules:
        if r.get("IsEgress"):
            continue
        proto = "all" if r["IpProtocol"] == "-1" else r["IpProtocol"]
        fp, tp = r.get("FromPort", -1), r.get("ToPort", -1)
        port = "all" if fp == -1 else (str(fp) if fp == tp else f"{fp}-{tp}")
        src = (r.get("CidrIpv4") or r.get("CidrIpv6") or r.get("PrefixListId")
               or (r.get("ReferencedGroupInfo") or {}).get("GroupId") or "?")
        out.append({"protocol": proto, "port": port, "source": src,
                    "description": r.get("Description", ""), "rule_id": r["SecurityGroupRuleId"]})
    return sorted(out, key=lambda x: (x["protocol"], x["port"], x["source"]))


def main():
    m = AnsibleModule(argument_spec=dict(
        region=dict(type="str", required=True),
        group_id=dict(type="str", required=True),
        action=dict(type="str", default="describe", choices=["describe", "add", "remove"]),
        protocol=dict(type="str", default="tcp"),
        port=dict(type="int"),
        cidr=dict(type="str"),
        description=dict(type="str", default=""),
    ), supports_check_mode=True)
    if not HAS_BOTO3:
        m.fail_json(msg="boto3 is required in the execution environment")
    p = m.params
    ec2 = boto3.client("ec2", region_name=p["region"])

    def snapshot():
        res = ec2.describe_security_group_rules(Filters=[{"Name": "group-id", "Values": [p["group_id"]]}])
        return normalize(res["SecurityGroupRules"])

    try:
        before = snapshot()
        if p["action"] == "describe":
            m.exit_json(changed=False, rules=before)
        if p["port"] is None or not p["cidr"]:
            m.fail_json(msg="port and cidr are required for add/remove")
        target = {"protocol": p["protocol"], "port": str(p["port"]), "source": p["cidr"]}
        exists = any(all(r[k] == v for k, v in target.items()) for r in before)
        want = p["action"] == "add"
        if exists == want:
            m.exit_json(changed=False, rules=before, msg="already in desired state")
        if not m.check_mode:
            rng = {"CidrIp": p["cidr"]} if ":" not in p["cidr"] else {"CidrIpv6": p["cidr"]}
            if want and p["description"]:
                rng["Description"] = p["description"][:255]
            perm = {"IpProtocol": p["protocol"], "FromPort": p["port"], "ToPort": p["port"]}
            perm["IpRanges" if ":" not in p["cidr"] else "Ipv6Ranges"] = [rng]
            if want:
                ec2.authorize_security_group_ingress(GroupId=p["group_id"], IpPermissions=[perm])
            else:
                ec2.revoke_security_group_ingress(GroupId=p["group_id"], IpPermissions=[perm])
        m.exit_json(changed=True, rules=snapshot() if not m.check_mode else before)
    except Exception as e:  # botocore ClientError, NoCredentials, etc.
        m.fail_json(msg=f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
