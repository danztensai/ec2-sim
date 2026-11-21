# Disposable tool runner for `ec2-sim run`: executes a project's own scripts
# (Packer builds, AWS CLI deploy scripts...) against ec2-sim. AWS traffic goes
# through ec2-sim's HTTPS proxy; only fake credentials exist in here.
FROM ubuntu:24.04
ARG PACKER_VERSION=1.16.2
ARG TARGETARCH
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl unzip jq git sudo openssh-client less make python3 debianutils gnupg \
 && rm -rf /var/lib/apt/lists/*
# Legacy Debian scripts call `tempfile` (dropped from newer debianutils).
RUN command -v tempfile >/dev/null || { printf '#!/bin/sh\nexec mktemp "${TMPDIR:-/tmp}/fileXXXXXX"\n' > /usr/local/bin/tempfile && chmod +x /usr/local/bin/tempfile; }
# HashiCorp Packer (checksum-verified) + the Amazon plugin.
RUN arch="${TARGETARCH:-$(dpkg --print-architecture)}" \
 && cd /tmp \
 && curl -fsSLO "https://releases.hashicorp.com/packer/${PACKER_VERSION}/packer_${PACKER_VERSION}_linux_${arch}.zip" \
 && curl -fsSL "https://releases.hashicorp.com/packer/${PACKER_VERSION}/packer_${PACKER_VERSION}_SHA256SUMS" | grep "linux_${arch}.zip" | sha256sum -c - \
 && unzip -q "packer_${PACKER_VERSION}_linux_${arch}.zip" -d /usr/local/bin && rm -f packer_* \
 && packer plugins install github.com/hashicorp/amazon
# AWS CLI v2.
RUN arch="$(uname -m)" && cd /tmp \
 && curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${arch}.zip" -o awscli.zip \
 && unzip -q awscli.zip && ./aws/install && rm -rf aws awscli.zip
COPY runner/entrypoint.sh /usr/local/bin/ec2sim-runner
RUN chmod +x /usr/local/bin/ec2sim-runner
ENTRYPOINT ["/usr/local/bin/ec2sim-runner"]
