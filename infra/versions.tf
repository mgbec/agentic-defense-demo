terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # The AgentCore resources (runtime, gateway, policy engine) need a recent 6.x provider.
      version = ">= 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "agentic-defense-demo"
      ManagedBy = "terraform"
    }
  }
}
