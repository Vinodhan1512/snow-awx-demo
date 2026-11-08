#!/usr/bin/env bash
# Run this in AWS CloudShell (console > CloudShell icon). No keys, no installs needed.
# Creates: IAM role + instance profile, host SG (no inbound), demo target SG, t3.large Ubuntu 22.04.
# Usage:  bash launch-ec2.sh            (region defaults to ap-south-1)
#         REGION=me-central-1 bash launch-ec2.sh
set -euo pipefail

REGION="${REGION:-ap-south-1}"
NAME="${NAME:-awx-demo}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.large}"
export AWS_DEFAULT_REGION="$REGION"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
VPC_ID=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
if [[ "$VPC_ID" == "None" || -z "$VPC_ID" ]]; then
  say "No default VPC in $REGION. Creating one."
  VPC_ID=$(aws ec2 create-default-vpc --query 'Vpc.VpcId' --output text)
fi
say "Account $ACCOUNT_ID, region $REGION, VPC $VPC_ID"

sg_id() { aws ec2 describe-security-groups --filters Name=group-name,Values="$1" Name=vpc-id,Values="$VPC_ID" \
          --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null; }

# --- Host SG: no inbound at all (access via SSM Session Manager) ---
HOST_SG=$(sg_id "${NAME}-host-sg")
if [[ "$HOST_SG" == "None" || -z "$HOST_SG" ]]; then
  HOST_SG=$(aws ec2 create-security-group --group-name "${NAME}-host-sg" --vpc-id "$VPC_ID" \
            --description "AWX demo host - no inbound, SSM only" --query GroupId --output text)
fi
say "Host SG: $HOST_SG"

# --- Target SG: the one ServiceNow change requests will modify ---
TARGET_SG=$(sg_id "${NAME}-target-sg")
if [[ "$TARGET_SG" == "None" || -z "$TARGET_SG" ]]; then
  TARGET_SG=$(aws ec2 create-security-group --group-name "${NAME}-target-sg" --vpc-id "$VPC_ID" \
              --description "Demo SG managed via ServiceNow + AWX" --query GroupId --output text)
  aws ec2 create-tags --resources "$TARGET_SG" --tags Key=Purpose,Value=snow-awx-demo
  # One baseline rule so the before/after table is not empty
  aws ec2 authorize-security-group-ingress --group-id "$TARGET_SG" --ip-permissions \
    'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=10.0.0.0/8,Description=baseline-https}]' >/dev/null
fi
say "Target SG: $TARGET_SG"

# --- IAM role: SSM access + SG change rights on the target SG only ---
ROLE="${NAME}-ec2-role"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name sg-change-demo --policy-document "{
  \"Version\":\"2012-10-17\",
  \"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":[\"ec2:DescribeSecurityGroups\",\"ec2:DescribeSecurityGroupRules\"],\"Resource\":\"*\"},
    {\"Effect\":\"Allow\",\"Action\":[\"ec2:AuthorizeSecurityGroupIngress\",\"ec2:RevokeSecurityGroupIngress\"],
     \"Resource\":\"arn:aws:ec2:${REGION}:${ACCOUNT_ID}:security-group/${TARGET_SG}\"}
  ]}"
if ! aws iam get-instance-profile --instance-profile-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE"
  say "Waiting 15s for the instance profile to propagate"
  sleep 15
fi

# --- Instance ---
EXISTING=$(aws ec2 describe-instances --filters Name=tag:Name,Values="$NAME" \
           Name=instance-state-name,Values=pending,running,stopped \
           --query 'Reservations[0].Instances[0].InstanceId' --output text)
if [[ "$EXISTING" != "None" && -n "$EXISTING" ]]; then
  INSTANCE_ID="$EXISTING"
  say "Instance already exists: $INSTANCE_ID (starting it if stopped)"
  aws ec2 start-instances --instance-ids "$INSTANCE_ID" >/dev/null || true
else
  AMI=$(aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id \
        --query Parameter.Value --output text)
  # Hop limit 2 lets AWX job pods (inside k3s) reach the instance role via IMDSv2.
  INSTANCE_ID=$(aws ec2 run-instances --image-id "$AMI" --instance-type "$INSTANCE_TYPE" \
    --iam-instance-profile Name="$ROLE" --security-group-ids "$HOST_SG" \
    --metadata-options HttpTokens=required,HttpPutResponseHopLimit=2,HttpEndpoint=enabled \
    --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=40,VolumeType=gp3}' \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
    --query 'Instances[0].InstanceId' --output text)
  say "Launched $INSTANCE_ID ($INSTANCE_TYPE, Ubuntu 22.04)"
fi
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"

cat <<EOF

=====================================================================
 Done. Put these in your .env:

   AWS_REGION=$REGION
   DEMO_SG_ID=$TARGET_SG

 Next: EC2 console > $INSTANCE_ID > Connect > Session Manager > Connect
 (give it 2-3 minutes after launch for the SSM agent to register), then:

   sudo -i
   git clone <your-repo-url> snow-awx-demo && cd snow-awx-demo
   cp .env.example .env && nano .env
   ./setup.sh

 Stop it when idle:   aws ec2 stop-instances --instance-ids $INSTANCE_ID
 Remove everything:   bash aws/destroy.sh
=====================================================================
EOF
