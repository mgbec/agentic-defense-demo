data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # One entry per tool. The gateway target name becomes the prefix the model sees
  # ("kb___search_kb"), and the same name appears in the Cedar action ids.
  tools = {
    search_kb = {
      target      = "kb"
      description = "Search the support knowledge base for company policies and help articles."
      writes_sink = false
      properties = [
        { name = "query", type = "string", required = true, description = "Search words, e.g. 'return policy'." },
      ]
    }
    lookup_customer = {
      target      = "crm"
      description = "Read a customer's record by CUSTOMER id (for example C-1001). Not an order id."
      writes_sink = false
      properties = [
        { name = "customer_id", type = "string", required = true, description = "Customer id such as C-1001." },
      ]
    }
    send_email = {
      target      = "mail"
      description = "Send an email to a recipient."
      writes_sink = true
      properties = [
        { name = "to", type = "string", required = true, description = "Recipient email address." },
        { name = "subject", type = "string", required = true, description = "Email subject." },
        { name = "body", type = "string", required = true, description = "Email body text." },
      ]
    }
    issue_refund = {
      target      = "billing"
      description = "Refund an order. Needs only the order id and a whole-dollar amount."
      writes_sink = true
      properties = [
        { name = "order_id", type = "string", required = true, description = "Order id such as A-1003." },
        { name = "amount", type = "integer", required = true, description = "Refund amount in whole US dollars." },
        { name = "reason", type = "string", required = true, description = "Why the refund is being issued." },
      ]
    }
  }

  # Every tool is exposed on both gateways: "open" (no policy engine) and "guarded".
  gateway_names = ["open", "guarded"]
  targets = {
    for pair in setproduct(local.gateway_names, keys(local.tools)) :
    "${pair[0]}.${pair[1]}" => { gateway = pair[0], tool = pair[1] }
  }
}
