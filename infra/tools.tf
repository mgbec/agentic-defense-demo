# Layer 5: least-privilege execution. Each tool is its own Lambda function with its own IAM
# role. The two read-only tools have no database access at all. The two side-effecting tools can
# write to the sink table and nothing else. Even if every other layer failed, a hijacked
# search_kb could not send an email or move money.

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "tool" {
  for_each = local.tools

  name               = "${var.name_prefix}-tool-${replace(each.key, "_", "-")}"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "tool_logs" {
  for_each = local.tools

  role       = aws_iam_role.tool[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "tool_sink_write" {
  statement {
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.sink.arn]
  }
}

resource "aws_iam_role_policy" "tool_sink_write" {
  for_each = { for name, tool in local.tools : name => tool if tool.writes_sink }

  name   = "write-sink"
  role   = aws_iam_role.tool[each.key].id
  policy = data.aws_iam_policy_document.tool_sink_write.json
}

resource "aws_cloudwatch_log_group" "tool" {
  for_each = local.tools

  name              = "/aws/lambda/${var.name_prefix}-${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "tool" {
  for_each = local.tools

  function_name = "${var.name_prefix}-${each.key}"
  role          = aws_iam_role.tool[each.key].arn
  runtime       = "python3.12"
  handler       = "${each.key}.handler"
  timeout       = 10
  memory_size   = 128
  filename      = "${var.dist_dir}/tools/${each.key}.zip"
  # fileexists() only lets `terraform validate` run without a build; apply still needs the zip.
  source_code_hash = fileexists("${var.dist_dir}/tools/${each.key}.zip") ? filebase64sha256("${var.dist_dir}/tools/${each.key}.zip") : null

  dynamic "environment" {
    for_each = each.value.writes_sink ? [1] : []
    content {
      variables = { SINK_TABLE = aws_dynamodb_table.sink.name }
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.tool,
    aws_iam_role_policy_attachment.tool_logs,
    aws_iam_role_policy.tool_sink_write,
  ]
}
