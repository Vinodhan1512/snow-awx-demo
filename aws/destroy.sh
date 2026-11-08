#!/usr/bin/env bash
# Removes everything launch-ec2.sh created. Run in AWS CloudShell.
set -uo pipefail
REGION="${REGION:-ap-south-1}"; NAME="${NAME:-awx-demo}"
export AWS_DEFAULT_REGION="$REGION"
ROLE="${NAME}-ec2-role"

IDS=$(aws ec2 describe-instances --filters Name=tag:Name,Values="$NAME" \
      Name=instance-state-name,Values=pending,running,stopping,stopped \
      --query 'Reservations[].Instances[].InstanceId' --output text)
if [[ -n "$IDS" ]]; then
  echo "Terminating $IDS"
  aws ec2 terminate-instances --instance-ids $IDS >/dev/null
  aws ec2 wait instance-terminated --instance-ids $IDS
fi
for g in "${NAME}-target-sg" "${NAME}-host-sg"; do
  id=$(aws ec2 describe-security-groups --filters Name=group-name,Values="$g" --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null)
  [[ "$id" != "None" && -n "$id" ]] && aws ec2 delete-security-group --group-id "$id" && echo "Deleted $g"
done
aws iam remove-role-from-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE" 2>/dev/null
aws iam delete-instance-profile --instance-profile-name "$ROLE" 2>/dev/null
aws iam delete-role-policy --role-name "$ROLE" --policy-name sg-change-demo 2>/dev/null
aws iam detach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore 2>/dev/null
aws iam delete-role --role-name "$ROLE" 2>/dev/null && echo "Deleted IAM role $ROLE"
echo "Teardown complete."
