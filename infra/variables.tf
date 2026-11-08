variable "region" {
  description = "AWS region. AgentCore guardrails-in-policy and Nova models are available in us-east-1."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for resource names. Letters, digits and hyphens only."
  type        = string
  default     = "agentic-defense"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,24}$", var.name_prefix))
    error_message = "name_prefix must be 2-25 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "model_id" {
  description = "Bedrock model the agent uses. Nova Lite is cheap, supports tool use, and needs no approval form."
  type        = string
  default     = "amazon.nova-lite-v1:0"
}

variable "dist_dir" {
  description = "Directory holding the build output of scripts/build.py (agent.zip and tools/*.zip)."
  type        = string
  default     = "../dist"
}

# These two values must match common/constants.py. tests/test_infra_consistency.py checks it.
variable "allowed_email_domain" {
  description = "The only email domain the gateway policy lets send_email reach."
  type        = string
  default     = "acme-demo.example"
}

variable "refund_limit_dollars" {
  description = "The largest refund (whole dollars) the gateway policy lets the assistant issue."
  type        = number
  default     = 50
}

variable "policy_mode" {
  description = "ENFORCE blocks calls the policy denies. LOG_ONLY records the decision but allows the call."
  type        = string
  default     = "ENFORCE"

  validation {
    condition     = contains(["ENFORCE", "LOG_ONLY"], var.policy_mode)
    error_message = "policy_mode must be ENFORCE or LOG_ONLY."
  }
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "budget_limit_usd" {
  description = "Monthly spend (whole USD) for the account-wide budget alert. Only used when budget_alert_email is set."
  type        = number
  default     = 10
}

variable "budget_alert_email" {
  description = "Email address to warn when spending approaches budget_limit_usd. Leave empty to skip creating a budget. The budget covers the whole account, not just this stack."
  type        = string
  default     = ""
}
