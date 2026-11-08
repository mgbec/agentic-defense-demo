# Guard rails around the people running the demo, not around the agent.

# ---- a policy for the presenter's machine ------------------------------------------------------
# The web page and `python -m redteam` run with your AWS credentials and can spend money. Attach
# this policy to a dedicated role or user for them instead of using an administrator. It can call
# this one runtime and read or clear this one table, and it cannot do anything else.
# (The Terraform does not create the role or user, because who assumes it is your decision.)
data "aws_iam_policy_document" "presenter" {
  statement {
    sid     = "InvokeTheAgent"
    actions = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = [
      aws_bedrockagentcore_agent_runtime.agent.agent_runtime_arn,
      "${aws_bedrockagentcore_agent_runtime.agent.agent_runtime_arn}/runtime-endpoint/*",
    ]
  }

  # The runner reads recorded emails and refunds to judge outcomes, and clears them between runs.
  statement {
    sid       = "ReadAndClearTheSink"
    actions   = ["dynamodb:Query", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.sink.arn]
  }
}

resource "aws_iam_policy" "presenter" {
  name        = "${var.name_prefix}-presenter"
  description = "Run the demo web page and red-team runner against this stack, and nothing else."
  policy      = data.aws_iam_policy_document.presenter.json
}

# ---- a spending alert --------------------------------------------------------------------------
# Nothing else caps spend. This is account-wide (a budget cannot be limited to one stack without
# cost-allocation tags) and only created when you give it an email address.
resource "aws_budgets_budget" "demo" {
  count = var.budget_alert_email == "" ? 0 : 1

  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}
