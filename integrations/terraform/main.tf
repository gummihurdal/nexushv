# NexusHV Terraform Provider — Example Configuration
# Provider source: registry.terraform.io/gummihurdal/nexushv

terraform {
  required_providers {
    nexushv = {
      source  = "gummihurdal/nexushv"
      version = "~> 1.0"
    }
  }
}

provider "nexushv" {
  endpoint = "https://nexushv.example.com:8080"
  username = "admin"
  password = var.nexushv_password
}

# Create a VM
resource "nexushv_vm" "web_server" {
  name     = "web-server-01"
  cpu      = 4
  ram_gb   = 8
  disk_gb  = 100
  os       = "ubuntu22.04"
  template = "template-ubuntu-22"

  network {
    name = "VM Network"
  }

  tags = ["production", "web", "team-alpha"]

  lifecycle {
    prevent_destroy = true
  }
}

# Create a storage container
resource "nexushv_storage_container" "production" {
  name               = "vm-production"
  replication_factor = 3
  compression        = true
  dedup              = true
  encryption         = false
}

# Snapshot policy
resource "nexushv_snapshot_policy" "daily" {
  vm_name        = nexushv_vm.web_server.name
  interval_hours = 24
  max_snapshots  = 7
}

# Output
output "vm_id" {
  value = nexushv_vm.web_server.id
}

variable "nexushv_password" {
  type      = string
  sensitive = true
}
