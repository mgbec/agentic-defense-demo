# Layer 4: tool authorization at the AgentCore Gateway.
#
# Two gateways expose the same four tools:
#   open     no policy engine. Anything the caller asks for goes through.
#   guarded  a policy engine in ENFORCE mode. Every tool call is checked against Cedar policy
#            OUTSIDE the model's reasoning, so a manipulated model cannot talk its way past it.
#
# The agent picks one per request based on the defense profile.

data "aws_iam_policy_document" "gateway_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock-agentcore:${var.region}:${local.account_id}:*"]
    }
  }
}

data "aws_iam_policy_document" "gateway_permissions" {
  statement {
    sid       = "InvokeToolFunctions"
    actions   = ["lambda:InvokeFunction"]
    resources = [for f in aws_lambda_function.tool : f.arn]
  }

  # Without these three actions every call is denied, even with permit policies in place.
  statement {
    sid       = "ReadPolicyEngine"
    actions   = ["bedrock-agentcore:GetPolicyEngine"]
    resources = [aws_bedrockagentcore_policy_engine.main.policy_engine_arn]
  }

  statement {
    sid     = "EvaluatePolicy"
    actions = ["bedrock-agentcore:AuthorizeAction", "bedrock-agentcore:PartiallyAuthorizeActions"]
    resources = [
      aws_bedrockagentcore_policy_engine.main.policy_engine_arn,
      # A wildcard avoids a dependency cycle (the gateways need this role to exist first).
      "arn:aws:bedrock-agentcore:${var.region}:${local.account_id}:gateway/*",
    ]
  }
}

resource "aws_iam_role" "gateway" {
  name               = "${var.name_prefix}-gateway"
  assume_role_policy = data.aws_iam_policy_document.gateway_trust.json
}

resource "aws_iam_role_policy" "gateway" {
  name   = "tools-and-policy"
  role   = aws_iam_role.gateway.id
  policy = data.aws_iam_policy_document.gateway_permissions.json
}

resource "aws_bedrockagentcore_policy_engine" "main" {
  # Letters, digits and underscores only.
  name        = replace("${var.name_prefix}_policy_engine", "-", "_")
  description = "Cedar policies for the support assistant's tool calls."
}

resource "aws_bedrockagentcore_gateway" "open" {
  name            = "${var.name_prefix}-open"
  description     = "Tools with no authorization policy. For comparison in the demo."
  role_arn        = aws_iam_role.gateway.arn
  authorizer_type = "AWS_IAM"
  protocol_type   = "MCP"

  # Detailed error text, so a policy denial is visible to the agent and in the trace.
  # Demo only: production gateways should not return this much detail to callers.
  exception_level = "DEBUG"

  depends_on = [aws_iam_role_policy.gateway]
}

resource "aws_bedrockagentcore_gateway" "guarded" {
  name            = "${var.name_prefix}-guarded"
  description     = "The same tools, behind a Cedar policy engine."
  role_arn        = aws_iam_role.gateway.arn
  authorizer_type = "AWS_IAM"
  protocol_type   = "MCP"
  exception_level = "DEBUG"

  policy_engine_configuration {
    arn  = aws_bedrockagentcore_policy_engine.main.policy_engine_arn
    mode = var.policy_mode
  }

  depends_on = [aws_iam_role_policy.gateway]
}

locals {
  gateway_ids = {
    open    = aws_bedrockagentcore_gateway.open.gateway_id
    guarded = aws_bedrockagentcore_gateway.guarded.gateway_id
  }
}

resource "aws_bedrockagentcore_gateway_target" "tool" {
  for_each = local.targets

  # "kb", "crm", "mail", "billing": becomes the prefix in tool names like "mail___send_email".
  name               = local.tools[each.value.tool].target
  gateway_identifier = local.gateway_ids[each.value.gateway]
  description        = local.tools[each.value.tool].description

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.tool[each.value.tool].arn

        tool_schema {
          inline_payload {
            name        = each.value.tool
            description = local.tools[each.value.tool].description

            input_schema {
              type = "object"

              dynamic "property" {
                for_each = local.tools[each.value.tool].properties
                content {
                  name        = property.value.name
                  type        = property.value.type
                  description = property.value.description
                  required    = property.value.required
                }
              }
            }
          }
        }
      }
    }
  }
}

# ---------------------------------------------------------------------------------------------
# Cedar policies for the guarded gateway. The engine is default-deny: a tool call is refused
# unless a permit matches. Only the agent's own runtime role may call anything.
#
# The policy text lives in infra/policies/<name>.cedar.tftpl so it is easy to review, and so
# the tests can render the very same files and run them through a real Cedar engine.
# ---------------------------------------------------------------------------------------------
locals {
  policy_values = {
    principal    = "AgentCore::IamEntity::\"arn:aws:sts::${local.account_id}:assumed-role/${aws_iam_role.runtime.name}\""
    resource     = "AgentCore::Gateway::\"${aws_bedrockagentcore_gateway.guarded.gateway_arn}\""
    email_domain = var.allowed_email_domain
    refund_limit = var.refund_limit_dollars
  }

  # policy file name => the tool it governs
  policies = {
    # Unconditional on purpose: reading help articles and customer records is allowed, so skip
    # the "always allows" lint finding for these two.
    permit_search_kb       = { tool = "search_kb", validation = "IGNORE_ALL_FINDINGS" }
    permit_lookup_customer = { tool = "lookup_customer", validation = "IGNORE_ALL_FINDINGS" }

    # Recipient must be a single address in the allowed domain. Matching only the suffix would
    # let "evil@bad.test, ok@<allowed domain>" through, so lists are rejected too.
    permit_send_email_internal = { tool = "send_email", validation = "FAIL_ON_ANY_FINDINGS" }

    permit_refund_up_to_limit = { tool = "issue_refund", validation = "FAIL_ON_ANY_FINDINGS" }
  }
}

resource "aws_bedrockagentcore_policy" "rule" {
  for_each = local.policies

  name             = each.key
  policy_engine_id = aws_bedrockagentcore_policy_engine.main.policy_engine_id
  description      = "Support assistant tool authorization"
  validation_mode  = each.value.validation

  definition {
    cedar {
      statement = templatefile("${path.module}/policies/${each.key}.cedar.tftpl", merge(
        local.policy_values,
        { action = "AgentCore::Action::\"${local.tools[each.value.tool].target}___${each.value.tool}\"" },
      ))
    }
  }

  # Policies are validated against the live gateway, so its targets must exist first.
  depends_on = [aws_bedrockagentcore_gateway_target.tool]
}