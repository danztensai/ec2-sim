# End-to-end AMI build against ec2-sim with Packer's real amazon-ebs builder.
packer {
  required_plugins {
    amazon = {
      source  = "github.com/hashicorp/amazon"
      version = ">= 1.3.0"
    }
  }
}

variable "endpoint" {
  type    = string
  default = "http://localhost:4566"
}

# Docker Desktop (macOS/Windows): instance IPs aren't reachable from the host,
# so connect through ec2-sim's SSH bastion. Empty = connect directly.
variable "bastion_host" {
  type    = string
  default = ""
}

variable "bastion_port" {
  type    = number
  default = 2222
}

source "amazon-ebs" "web" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  custom_endpoint_ec2         = var.endpoint
  skip_credential_validation  = true
  skip_region_validation      = true
  skip_metadata_api_check     = true

  source_ami_filter {
    owners      = ["amazon"]
    most_recent = true
    filters = {
      name                = "al2023-ami-*ec2sim*"
      root-device-type    = "ebs"
      virtualization-type = "hvm"
    }
  }

  instance_type = "t3.micro"
  ssh_username  = "ec2-user"
  ssh_interface = "public_ip"

  ssh_bastion_host     = var.bastion_host
  ssh_bastion_port     = var.bastion_port
  ssh_bastion_username = "ec2sim"
  ssh_bastion_password = "ec2sim"
  ami_name      = "web-al2023-{{timestamp}}"
  tags = {
    Builder = "packer"
    Role    = "web"
  }
}

build {
  sources = ["source.amazon-ebs.web"]

  provisioner "shell" {
    inline = [
      "sudo dnf -y install nginx",
      "echo 'built by packer on ec2-sim' | sudo tee /usr/share/nginx/html/index.html",
      "nginx -v",
    ]
  }

  post-processor "manifest" {
    output = "packer-manifest.json"
  }
}
