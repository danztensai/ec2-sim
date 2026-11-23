#!/bin/bash
set -ex
apt-get update && apt-get install -y curl ca-certificates
useradd -r -m -d /opt/elasticsearch es || true
curl -fsSL https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-8.5.3-linux-x86_64.tar.gz \
  | tar xz -C /opt/elasticsearch --strip-components=1
cat >> /opt/elasticsearch/config/elasticsearch.yml <<YML
network.host: 0.0.0.0
discovery.type: single-node
xpack.security.enabled: false
node.store.allow_mmap: false
YML
chown -R es:es /opt/elasticsearch
cat > /etc/systemd/system/elasticsearch.service <<UNIT
[Unit]
After=network.target
[Service]
User=es
Environment=ES_JAVA_OPTS=-Xms512m -Xmx512m
ExecStart=/opt/elasticsearch/bin/elasticsearch
LimitNOFILE=65535
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now elasticsearch
