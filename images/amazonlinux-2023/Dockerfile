FROM public.ecr.aws/amazonlinux/amazonlinux:2023
RUN dnf -y install openssh-server openssh-clients sudo shadow-utils procps-ng iproute tar gzip findutils hostname \
 && dnf clean all \
 && useradd -m -s /bin/bash ec2-user \
 && echo 'ec2-user ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-ec2-user \
 && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
COPY common/ec2sim-init /usr/local/sbin/ec2sim-init
RUN chmod +x /usr/local/sbin/ec2sim-init
ENV EC2SIM_DEFAULT_USER=ec2-user
EXPOSE 22
ENTRYPOINT ["/usr/local/sbin/ec2sim-init"]
