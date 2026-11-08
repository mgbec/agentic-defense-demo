# The "sink": records what send_email and issue_refund actually did, so the red-team runner can
# check outcomes from ground truth instead of trusting the model's own account. See common/sink.py.

resource "aws_dynamodb_table" "sink" {
  name         = "${var.name_prefix}-sink"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "kind"
  range_key    = "id"

  attribute {
    name = "kind"
    type = "S"
  }

  attribute {
    name = "id"
    type = "S"
  }

  # Demo data: easy to remove with `terraform destroy`.
  deletion_protection_enabled = false
}
