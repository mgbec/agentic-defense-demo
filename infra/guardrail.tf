# Layer 1: Amazon Bedrock Guardrails. Used by the agent through the ApplyGuardrail API, on the
# user's message, on the final answer, and (profile "isolation" and up) on retrieved documents.
#
# CLASSIC tier on purpose: STANDARD needs cross-region inference, which is a data-residency
# decision that should not be made silently by a demo.

resource "aws_bedrock_guardrail" "main" {
  name                      = "${var.name_prefix}-guardrail"
  description               = "Content, prompt-attack and data-loss filters for the support assistant demo."
  blocked_input_messaging   = "Sorry, I can't help with that request."
  blocked_outputs_messaging = "Sorry, I can't share that information."

  content_policy_config {
    dynamic "filters_config" {
      for_each = ["HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT"]
      content {
        type            = filters_config.value
        input_strength  = "HIGH"
        output_strength = "HIGH"
      }
    }

    # Detects jailbreaks and prompt injection. Applies to input only; the API requires NONE
    # for output.
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }

    tier_config {
      tier_name = "CLASSIC"
    }
  }

  sensitive_information_policy_config {
    # Masked rather than blocked, so a helpful answer still goes out without the number.
    dynamic "pii_entities_config" {
      for_each = ["US_SOCIAL_SECURITY_NUMBER", "CREDIT_DEBIT_CARD_NUMBER"]
      content {
        type           = pii_entities_config.value
        action         = "ANONYMIZE"
        input_action   = "ANONYMIZE"
        output_action  = "ANONYMIZE"
        input_enabled  = true
        output_enabled = true
      }
    }

    # A data-loss-prevention rule: anything shaped like one of our internal canaries must
    # never leave in an answer. Catches both the system-prompt secret and the account note.
    regexes_config {
      name           = "internal_canary"
      description    = "Internal reference codes and notes must not appear in answers."
      pattern        = "CANARY-[A-Z]+-[0-9a-f]{4}"
      action         = "BLOCK"
      input_action   = "NONE"
      output_action  = "BLOCK"
      input_enabled  = false
      output_enabled = true
    }
  }
}

# An immutable, numbered version. The agent pins to this rather than the editable DRAFT.
resource "aws_bedrock_guardrail_version" "main" {
  guardrail_arn = aws_bedrock_guardrail.main.guardrail_arn
  description   = "Managed by Terraform"
  skip_destroy  = false

  lifecycle {
    replace_triggered_by = [aws_bedrock_guardrail.main]
  }
}
