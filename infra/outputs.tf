# Read by the red-team runner and the web page (see redteam/config.py).

output "region" {
  value = var.region
}

output "agent_runtime_arn" {
  description = "Invoke this runtime with bedrock-agentcore InvokeAgentRuntime."
  value       = aws_bedrockagentcore_agent_runtime.agent.agent_runtime_arn
}

output "sink_table" {
  description = "DynamoDB table recording what the side-effecting tools did."
  value       = aws_dynamodb_table.sink.name
}

output "guardrail_id" {
  value = aws_bedrock_guardrail.main.guardrail_id
}

output "guardrail_version" {
  value = aws_bedrock_guardrail_version.main.version
}

output "gateway_url_open" {
  value = aws_bedrockagentcore_gateway.open.gateway_url
}

output "gateway_url_guarded" {
  value = aws_bedrockagentcore_gateway.guarded.gateway_url
}

output "model_id" {
  value = var.model_id
}

output "presenter_policy_arn" {
  description = "Attach this to the role or user that runs the web page and red-team runner."
  value       = aws_iam_policy.presenter.arn
}
