# Ubuntu 18.04 (bionic) AMI stand-in with systemd as PID 1, shaped like the
# Ubuntu cloud image: ubuntu user (sudo, NOPASSWD), cloud-guest-utils
# (ec2metadata), rsyslog, apt https transport.
FROM ubuntu:18.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      systemd systemd-sysv dbus openssh-server sudo rsyslog cron \
      ca-certificates apt-transport-https gnupg curl wget unzip lsb-release \
      iproute2 iputils-ping less nano cloud-guest-utils python3 debianutils \
      software-properties-common tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -f admin \
 && useradd -m -s /bin/bash -G adm,sudo,admin ubuntu \
 && echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-cloud-init-users \
 && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config \
 && rm -f /etc/ssh/ssh_host_* \
 && systemctl mask getty@tty1.service console-getty.service systemd-logind.service \
      sys-kernel-config.mount sys-kernel-debug.mount systemd-remount-fs.service \
 && systemctl enable ssh.service
COPY common/ec2sim-boot common/ec2sim-userdata /usr/local/sbin/
COPY common/ec2sim-userdata.service /etc/systemd/system/
RUN chmod +x /usr/local/sbin/ec2sim-boot /usr/local/sbin/ec2sim-userdata \
 && systemctl enable ec2sim-userdata.service
ENV EC2SIM_DEFAULT_USER=ubuntu
LABEL ec2sim.systemd="true"
STOPSIGNAL SIGRTMIN+3
EXPOSE 22
ENTRYPOINT ["/usr/local/sbin/ec2sim-boot"]
