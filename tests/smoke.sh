#!/usr/bin/env bash
# End-to-end smoke test against a running ec2-sim (default http://localhost:4566).
set -euo pipefail
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1
EP=${EC2SIM_ENDPOINT:-http://localhost:4566}
ec2() { aws --endpoint-url "$EP" ec2 "$@"; }
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
BASE=ami-0ec2511a120230001
ssh_() { ssh -i "$WORK/key.pem" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=3 "$@"; }
wait_ssh() { for _ in $(seq 1 30); do ssh_ "ec2-user@$1" true 2>/dev/null && return 0; sleep 1; done; echo "ssh to $1 failed"; return 1; }

echo "== base AMIs"; ec2 describe-images --owners amazon 099720109477 --filters Name=name,Values='*ec2sim*' --query 'Images[].[ImageId,Name,RootDeviceType]' --output text
ec2 create-key-pair --key-name smoke --query KeyMaterial --output text > "$WORK/key.pem"; chmod 600 "$WORK/key.pem"
printf '#!/bin/bash\necho "hello from user-data" > /tmp/ud.txt\n' > "$WORK/ud.sh"

echo "== RunInstances from base AMI"
IID=$(ec2 run-instances --image-id $BASE --instance-type t3.micro --key-name smoke --user-data "file://$WORK/ud.sh" --query 'Instances[0].InstanceId' --output text)
IP=$(ec2 describe-instances --instance-ids "$IID" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "$IID at $IP"; wait_ssh "$IP"
sleep 1; ssh_ "ec2-user@$IP" 'cat /tmp/ud.txt; hostname; sudo -n true && echo sudo-ok'

echo "== provision + StopInstances + CreateImage"
ssh_ "ec2-user@$IP" 'echo baked-$(date +%s) | sudo tee /etc/baked >/dev/null'
ec2 stop-instances --instance-ids "$IID" >/dev/null
AMI=$(ec2 create-image --instance-id "$IID" --name "smoke-$(date +%s)" --query ImageId --output text)
ec2 describe-images --image-ids "$AMI" --query 'Images[0].[ImageId,Name,State,ImageLocation]' --output text
ec2 terminate-instances --instance-ids "$IID" >/dev/null

echo "== RunInstances from the new AMI"
IID2=$(ec2 run-instances --image-id "$AMI" --instance-type t3.micro --key-name smoke --query 'Instances[0].InstanceId' --output text)
IP2=$(ec2 describe-instances --instance-ids "$IID2" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
wait_ssh "$IP2"; ssh_ "ec2-user@$IP2" 'cat /etc/baked; test ! -f /tmp/ud.txt.x && echo instance-ok'
ec2 terminate-instances --instance-ids "$IID2" >/dev/null

echo "== DeregisterImage"
ec2 deregister-image --image-id "$AMI"
docker image inspect "ec2sim/ami:$AMI" >/dev/null 2>&1 && { echo "image still present"; exit 1; } || echo "image removed"
ec2 delete-key-pair --key-name smoke
echo "SMOKE OK"
