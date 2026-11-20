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
"$PACKER" build -color=false -var "endpoint=$AWS_ENDPOINT_URL" al2023-web.pkr.hcl
