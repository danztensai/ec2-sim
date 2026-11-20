FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server sudo ca-certificates curl iproute2 \
 && rm -rf /var/lib/apt/lists/* \
 && (id ubuntu >/dev/null 2>&1 || useradd -m -s /bin/bash ubuntu) \
 && echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-ubuntu \
 && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
COPY common/ec2sim-init /usr/local/sbin/ec2sim-init
RUN chmod +x /usr/local/sbin/ec2sim-init
ENV EC2SIM_DEFAULT_USER=ubuntu
EXPOSE 22
ENTRYPOINT ["/usr/local/sbin/ec2sim-init"]
