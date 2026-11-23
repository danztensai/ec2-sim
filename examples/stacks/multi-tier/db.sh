#!/bin/bash
set -ex
export DEBIAN_FRONTEND=noninteractive
apt-get update && apt-get install -y curl gnupg ca-certificates
curl -fsSL https://pgp.mongodb.com/server-4.4.asc | apt-key add -
echo "deb [arch=amd64] https://repo.mongodb.org/apt/ubuntu bionic/mongodb-org/4.4 multiverse" \
  > /etc/apt/sources.list.d/mongodb-org-4.4.list
apt-get update && apt-get install -y mongodb-org redis-server
sed -i 's/^\s*bindIp:.*/  bindIp: 0.0.0.0/' /etc/mongod.conf
sed -i 's/^bind 127.*/bind 0.0.0.0/; s/^protected-mode yes/protected-mode no/' /etc/redis/redis.conf
systemctl enable --now mongod
systemctl restart redis-server
