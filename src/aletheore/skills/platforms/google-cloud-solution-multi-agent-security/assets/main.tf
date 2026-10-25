# Terraform HCL Configuration for Hybrid VPN & PSC Egress Routing

terraform {
  required_version = ">= 1.0.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 4.0.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  type        = string
  description = "GCP Project ID"
}

variable "region" {
  type        = string
  description = "GCP Region"
  default     = "us-central1"
}

variable "aws_vpc_cidr" {
  type        = string
  description = "AWS VPC CIDR block"
  default     = "10.100.0.0/16"
}

variable "aws_dns_resolver_ip" {
  type        = string
  description = "AWS Route 53 Inbound Resolver IP"
  default     = "10.100.0.2"
}

# 1. VPC and Subnet Config
resource "google_compute_network" "vpc" {
  name                    = "agent-gateway-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subnet" {
  name                     = "agent-gateway-subnet"
  ip_cidr_range            = "10.0.0.0/24"
  network                  = google_compute_network.vpc.id
  region                   = var.region
  private_ip_google_access = true # MANDATORY: Required for PSC Network Attachment
}

# 2. PSC Network Attachment (used by Egress Gateway)
resource "google_compute_network_attachment" "network_attachment" {
  name                  = "agw-egress-network-attachment"
  region                = var.region
  connection_preference = "ACCEPT_AUTOMATIC"
  subnetworks           = [google_compute_subnetwork.subnet.id]
}

# 3. Cloud DNS Private Forwarding Zone for aws.internal.
resource "google_dns_managed_zone" "aws_dns_zone" {
  name        = "aws-internal-zone"
  dns_name    = "aws.internal."
  description = "Forward queries for aws.internal to AWS Route 53 resolvers"
  visibility  = "private"

  private_visibility_config {
    networks {
      network_url = google_compute_network.vpc.id
    }
  }

  forwarding_config {
    target_name_servers {
      ipv4_address = var.aws_dns_resolver_ip
    }
  }
}

# 4. HA VPN Gateway
resource "google_compute_ha_vpn_gateway" "ha_vpn" {
  name    = "gcp-to-aws-vpn-gw"
  network = google_compute_network.vpc.id
  region  = var.region
}

# 5. Cloud Router (for BGP exchange)
resource "google_compute_router" "router" {
  name    = "vpn-router"
  network = google_compute_network.vpc.id
  region  = var.region
  bgp {
    asn = 65001
  }
}
