# SSH jump host for hosts that can't route to container IPs (Docker Desktop on
# macOS/Windows). Attached to every ec2-sim network; published on 127.0.0.1 only.
FROM alpine:3.20
RUN apk add --no-cache openssh-server \
 && adduser -D -s /bin/sh ec2sim \
 && echo 'ec2sim:ec2sim' | chpasswd \
 && printf '%s\n' \
      'Port 22' 'PasswordAuthentication yes' 'KbdInteractiveAuthentication no' \
      'AllowTcpForwarding yes' 'PermitTunnel no' 'X11Forwarding no' \
      'AllowUsers ec2sim' 'PidFile /run/sshd.pid' > /etc/ssh/sshd_config
COPY bastion/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]
