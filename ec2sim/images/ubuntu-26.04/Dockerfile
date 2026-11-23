# Ubuntu 26.04 (resolute) AMI stand-in with systemd as PID 1, shaped like the
# Ubuntu cloud image: ubuntu user (sudo, NOPASSWD), cloud-guest-utils
# (ec2metadata), rsyslog, curl/gnupg for adding apt repos.
FROM ubuntu:26.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      systemd systemd-sysv dbus openssh-server sudo rsyslog cron \
      ca-certificates gnupg curl wget unzip lsb-release \
      iproute2 iputils-ping less nano cloud-guest-utils python3 \
      software-properties-common tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -f admin \
 && (id ubuntu >/dev/null 2>&1 || useradd -m -s /bin/bash ubuntu) \
 && usermod -aG adm,sudo,admin ubuntu \
 && echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-cloud-init-users \
 && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config \
 && rm -f /etc/ssh/ssh_host_* \
 && systemctl mask getty@tty1.service console-getty.service systemd-logind.service \
      sys-kernel-config.mount sys-kernel-debug.mount systemd-remount-fs.service systemd-modules-load.service \
 && systemctl mask systemd-binfmt.service proc-sys-fs-binfmt_misc.automount proc-sys-fs-binfmt_misc.mount \
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
