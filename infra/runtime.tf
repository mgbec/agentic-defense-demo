# The agent itself, hosted on AgentCore Runtime as a zip of Python code (no container or Docker).
# scripts/build.py produces dist/agent.zip with linux/arm64 wheels, which is all the runtime runs.

resource "aws_s3_bucket" "agent_code" {
  bucket        = "${var.name_prefix}-agent-code-${local.account_id}-${var.region}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "agent_code" {
  bucket                  = aws_s3_bucket.agent_code.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "agent_code" {
  bucket = aws_s3_bucket.agent_code.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Versioning lets the runtime be pointed at an exact object version, so a new build redeploys.
resource "aws_s3_bucket_versioning" "agent_code" {
  bucket = aws_s3_bucket.agent_code.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_object" "agent_zip" {
  bucket = aws_s3_bucket.agent_code.id
  key    = "agent/agent.zip"
  source = "${var.dist_dir}/agent.zip"
  etag   = fileexists("${var.dist_dir}/agent.zip") ? filemd5("${var.dist_dir}/agent.zip") : null

  depends_on = [aws_s3_bucket_versioning.agent_code]
}

# ---- the runtime's execution role -------------------------------------------------------------
data "aws_iam_policy_document" "runtime_trust" {
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

data "aws_iam_policy_document" "runtime_permissions" {
  statement {
    sid     = "InvokeTheModel"
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    # Only the configured model. If you switch to a model that needs a cross-region inference
    # profile, add that profile's ARN here too (the call is authorized against both).
    resources = ["arn:aws:bedrock:${var.region}::foundation-model/${var.model_id}"]
  }

  statement {
    sid       = "ApplyOnlyThisGuardrail"
    actions   = ["bedrock:ApplyGuardrail"]
    resources = [aws_bedrock_guardrail.main.guardrail_arn]
  }

  # This role is the Cedar principal. The guarded gateway only permits tool calls from it.
  statement {
    sid     = "CallOnlyTheseGateways"
    actions = ["bedrock-agentcore:InvokeGateway"]
    resources = [
      aws_bedrockagentcore_gateway.open.gateway_arn,
      aws_bedrockagentcore_gateway.guarded.gateway_arn,
    ]
  }

  statement {
    sid = "WriteLogs"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"]
  }

  statement {
    sid       = "DescribeLogGroups"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:*"]
  }
}

resource "aws_iam_role" "runtime" {
  name               = "${var.name_prefix}-agent-runtime"
  assume_role_policy = data.aws_iam_policy_document.runtime_trust.json
}

resource "aws_iam_role_policy" "runtime" {
  name   = "agent"
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.runtime_permissions.json
}

# ---- the runtime ------------------------------------------------------------------------------
resource "aws_bedrockagentcore_agent_runtime" "agent" {
  # Letters, digits and underscores only.
  agent_runtime_name = replace("${var.name_prefix}_support_agent", "-", "_")
  description        = "Support assistant with switchable defense layers."
  role_arn           = aws_iam_role.runtime.arn

  agent_runtime_artifact {
    code_configuration {
      entry_point = ["main.py"]
      runtime     = "PYTHON_3_13"

      code {
        s3 {
          bucket     = aws_s3_bucket.agent_code.id
          prefix     = aws_s3_object.agent_zip.key
          version_id = aws_s3_object.agent_zip.version_id
        }
      }
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  environment_variables = {
    DEMO_REGION         = var.region
    MODEL_ID            = var.model_id
    GUARDRAIL_ID        = aws_bedrock_guardrail.main.guardrail_id
    GUARDRAIL_VERSION   = aws_bedrock_guardrail_version.main.version
    GATEWAY_URL_OPEN    = aws_bedrockagentcore_gateway.open.gateway_url
    GATEWAY_URL_GUARDED = aws_bedrockagentcore_gateway.guarded.gateway_url
  }

  depends_on = [aws_iam_role_policy.runtime]
}
