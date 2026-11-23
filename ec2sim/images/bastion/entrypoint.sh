#!/bin/sh
# Optional key auth: EC2SIM_BASTION_PUBKEY = one or more OpenSSH public keys.
set -e
ssh-keygen -A >/dev/null
if [ -n "${EC2SIM_BASTION_PUBKEY:-}" ]; then
  mkdir -p /home/ec2sim/.ssh
  printf '%s\n' "$EC2SIM_BASTION_PUBKEY" > /home/ec2sim/.ssh/authorized_keys
  chown -R ec2sim:ec2sim /home/ec2sim/.ssh
  chmod 700 /home/ec2sim/.ssh; chmod 600 /home/ec2sim/.ssh/authorized_keys
fi
exec /usr/sbin/sshd -D -e
