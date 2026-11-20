#!/usr/bin/env bash
# Build an AMI with Packer's real amazon-ebs builder against a running ec2-sim.
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(cd ../.. && pwd)
PACKER=${PACKER:-$(command -v packer || echo "$ROOT/.bin/packer")}
export PACKER_PLUGIN_PATH=${PACKER_PLUGIN_PATH:-$ROOT/.bin/plugins}
export AWS_ENDPOINT_URL=${EC2SIM_ENDPOINT:-http://localhost:4566}
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test
"$PACKER" init al2023-web.pkr.hcl
# EC2SIM_BASTION=127.0.0.1 (or host:port) when ec2-sim runs in bastion mode (Docker Desktop).
BASTION_ARGS=()
if [[ -n ${EC2SIM_BASTION:-} ]]; then
  BASTION_ARGS=(-var "bastion_host=${EC2SIM_BASTION%%:*}")
  [[ $EC2SIM_BASTION == *:* ]] && BASTION_ARGS+=(-var "bastion_port=${EC2SIM_BASTION##*:}")
fi
"$PACKER" build -color=false -var "endpoint=$AWS_ENDPOINT_URL" "${BASTION_ARGS[@]}" al2023-web.pkr.hcl
