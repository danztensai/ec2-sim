#!/bin/sh
# Trust the ec2-sim proxy CA (mounted at /ec2sim-ca.crt), then run the command.
set -e
if [ -f /ec2sim-ca.crt ]; then
  cp /ec2sim-ca.crt /usr/local/share/ca-certificates/ec2sim.crt
  update-ca-certificates >/dev/null 2>&1
fi
exec "$@"
