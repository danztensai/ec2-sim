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
