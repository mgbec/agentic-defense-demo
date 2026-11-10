# Defense in depth for an AI agent

A small customer-support agent on AWS, with four switchable defense profiles and a red-team suite that shows what each layer stops. It is built to be run live in front of developers and security people.

The agent runs on Amazon Bedrock AgentCore. Its four tools (search a knowledge base, look up a customer, send an email, issue a refund) sit behind AgentCore Gateway. The tools are simulated: emails and refunds are recorded in a DynamoDB table and never sent or paid.

## The layers

Each profile adds one layer to the one before it.

| Profile | Adds | Good at | Blind to |
|---|---|---|---|
| `none` | Nothing. Model and prompt only. | - | Everything the model can be talked into |
| `guardrail` | Bedrock Guardrails on the user message and the reply (prompt-attack and content filters, SSN/card masking) | Obvious jailbreaks, PII leaking out | Text that looks harmless but carries intent |
| `isolation` | Retrieved documents are screened and fenced as untrusted data; hardened system prompt | Instructions hidden in documents | Anything that is not in a document |
| `policy` | Cedar policy enforced by the gateway on every tool call | Over-limit refunds, email to outside domains. The model cannot argue with it | Harm inside what the policy allows |

No layer is complete. That is the point of stacking them.

## The red-team suite

`attacks/attacks.yaml` holds 14 entries of three kinds:

- **attack**: should hurt a weak setup (PII leak, poisoned document, social-engineered refund, email exfiltration).
- **control**: blatant attacks that the model already resists. They show where the baseline is fine, so the demo does not overclaim.
- **benign**: legitimate requests. They measure false positives, because a defense that blocks real work has a cost.

Each attack records a `hypothesis`, the layer expected to stop it, written before the runs. Whether a run caused harm is decided by a deterministic check, not another LLM: what the tools recorded in the sink table, or a planted canary string in the reply. A run that crashes counts as an error and is left out of the rates. It never counts as "the defense held".

Model behavior is random. With a few repeats per cell the numbers are noisy. They illustrate, they do not prove.

## Layout

```
agent/       AgentCore runtime entrypoint, Strands agent, defenses/ (guardrail, isolation, hooks)
tools/       Lambda handlers behind the gateway: search_kb, lookup_customer, send_email, issue_refund
common/      Shared constants, fake data, DynamoDB sink
infra/       Terraform (runtime, gateway, Cedar policies, guardrail, Lambdas, sink table)
attacks/     Attack definitions (YAML)
redteam/     Runner, oracles, scoreboard report, CLI
web/         Local web page (stdlib server + vanilla JS)
scripts/     build.py (deployment zips), probe_model.py, make_sample_results.py
tests/       Unit tests; none need AWS credentials
```

## Quick start

Needs Python 3.12+, [uv](https://docs.astral.sh/uv/), Terraform, and AWS credentials for a sandbox account.

### 1. Try it without AWS

```
python -m venv .venv
.venv\Scripts\activate            # Windows. On macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
python -m web --offline           # opens on http://localhost:8000, shows a sample scoreboard
```

The offline scoreboard is invented sample data, and the page says so. It cannot send messages or run attacks.

### 2. Deploy

```
python scripts/build.py           # writes dist/agent.zip and dist/tools/*.zip (arm64 wheels via uv, no Docker)
cd infra
terraform init
terraform plan                    # read it: about 47 resources (48 with a budget)
terraform apply
```

Region is `us-east-1` and the model is `amazon.nova-lite-v1:0`; both are Terraform variables.

To get a spending alert, add `-var budget_alert_email=you@example.com`. The identity running Terraform needs more than the usual create permissions: `s3:GetObject` on the code bucket the config creates, and `bedrock-agentcore:InvokeGateway` on the guarded gateway. Policy creation validates the Cedar against the live gateway.

### 3. Attack it

Run every command below from the project's virtual environment (`.venv\Scripts\activate`, or call `.venv\Scripts\python.exe` directly). A global Python usually has an older `boto3` that does not know the `bedrock-agentcore` service, and the tools stop with a message saying so.

The stack deploys in **demo mode** by default, so all four profiles work. There is an optional **lockdown** that serves only the fully defended `policy` profile. Use it if other people or roles in the account can call the agent:

```
python scripts/mode.py lock       # only the policy profile (runs terraform apply; shows a plan and asks first)
python scripts/mode.py demo       # all four profiles on
python scripts/mode.py status
```

Run `python scripts/build.py` once before using `mode.py`, because Terraform compares the build output with what is deployed. A plain `terraform apply` returns to demo mode, because that is the default.

```
python -m redteam list
python -m redteam run --quick                   # 8 attacks x 4 profiles x 3 repeats
python -m redteam run --profiles none,policy --repeats 5
python -m redteam show results/<file>.json
python -m web                                   # same thing in the browser, plus a chat box
```

The runner reads settings from `terraform output`, or from `DEMO_RUNTIME_ARN`, `DEMO_SINK_TABLE` and `DEMO_REGION`.

### 4. Tear down

```
cd infra
terraform destroy
```

## Cost

Nova Lite is priced per token and each agent run is a handful of short calls, so a quick scoreboard (96 runs by default) costs very little. Guardrail checks, AgentCore, Lambda and DynamoDB (on-demand) are billed by use. Idle cost is small, but not zero; destroy the stack when you are done. Check current pricing before a big run. The web page caps a job at 600 runs.

## The web page's security

It holds your AWS credentials and can spend money, so it binds to `127.0.0.1` only, checks the `Host` and `Origin` headers, requires a per-launch token on every POST, serves a strict Content-Security-Policy, limits body size and never inserts model text as HTML. Do not expose it beyond your machine.

## Before you deploy: security checklist

This stack includes an unguarded gateway and an agent that is meant to be manipulated. Treat it as a demo.

**Do these before `terraform apply`**

- [ ] **Use a sandbox account and short-lived credentials** (SSO or an assumed role), not a long-lived IAM user key and not a production account.
- [ ] **Turn on the budget alert.** Nothing else caps spend except the web page's 600-run limit per job and the agent's limit of six tool calls per request. Set `budget_alert_email` (and optionally `budget_limit_usd`, default 10) and Terraform creates a monthly budget that emails you at 80% of actual spend and when forecast spend passes 100%. It covers the whole account, not just this stack, and is skipped when the email is empty. The identity running Terraform needs `budgets:ModifyBudget` and `budgets:ViewBudget`.
- [ ] **Run the web page and red-team runner with the presenter policy.** They use your current AWS credentials and can spend money. Terraform outputs `presenter_policy_arn`, a policy that allows only `InvokeAgentRuntime` on this runtime and `Query` and `DeleteItem` on the sink table. Attach it to a dedicated role or user and use that instead of an administrator. Terraform does not create the role or user.

Already done in the Terraform: the runtime role may invoke only the model in `var.model_id`, not every foundation model. If you switch to a model that needs a cross-region inference profile, add that profile's ARN to the policy in `infra/runtime.tf`.

**Know about these**

- **Lockdown** (`var.lockdown`, off by default) makes the deployment serve only the fully defended profile. The agent refuses the `none`, `guardrail` and `isolation` profiles, and the runtime role loses permission to call the unguarded `open` gateway. Switch it with `python scripts/mode.py lock|demo`.
- Lockdown gives little protection on its own. Calling the runtime already requires AWS IAM credentials with `bedrock-agentcore:InvokeAgentRuntime`, so outsiders are blocked either way, and anyone holding your admin credentials can switch it off. It only limits what other identities you have granted access to can do. It does not cap spend (use the budget alert), and it does not stop direct calls to the open gateway by an identity with `bedrock-agentcore:InvokeGateway`.
- The `open` gateway has no policy engine on purpose. In demo mode the runtime role can call it. The tools are simulated, so the worst outcome is rows in the sink table. Run `terraform destroy` when you finish.
- Both gateways use `exception_level = "DEBUG"`, which returns detailed errors to callers. Production gateways should not.
- The runtime uses `network_mode = "PUBLIC"`, so a hijacked agent could reach the internet. Its role is narrow and holds no secrets. For stricter setups, use VPC mode with limited egress.
- Inbound calls to the runtime should be IAM (SigV4) authorized, because the Terraform configures no other authorizer. Confirm this in the console after the first apply.
- Not hardened, because the data is fake: no TLS-only bucket policy, no DynamoDB point-in-time recovery or customer-managed key, and the broad AWS-managed Lambda logging policy.
- Reserved Lambda concurrency is not set. New accounts often have a low concurrency quota, and setting it can make the apply fail.

**If you fork or publish this repo**

- Turn on secret scanning and push protection.
- Add Dependabot for pip, Terraform and GitHub Actions. The agent's dependencies are pinned, so they will not update on their own.
- Pin the workflow actions to commit SHAs and require CI before merging to `main`.
- Never commit Terraform state or `*.tfvars`. Both are in `.gitignore`.

## What is verified, and what is not

Verified locally (CI runs the same checks):

- Unit tests, including the Cedar policies run through a real Cedar engine (`cedarpy`), the oracles, the runner, the report and the web server.
- `terraform fmt`, `validate`, and a `plan` that adds 47 resources (48 with a budget).
- The deployment zips are built for arm64 and contain the expected native libraries.
- Nova Lite was called live to confirm it makes a correct tool call, and attack behavior was measured with a local copy of the agent (not the deployed one).

Verified on a real deployment (us-east-1, one run of the stack):

- AgentCore accepted all four Cedar policies when validating them against the live gateway.
- The runtime accepted the zip and environment variables, and the SigV4 MCP session works: the agent called tools through both gateways.
- A policy denial reaches the agent as "Tool call not allowed due to policy enforcement", and the `gateway_policy` event is recorded. In a small refund test (4 runs per cell) over-limit refunds went through 6 of 8 times with no defenses and 0 of 8 with the policy profile.
- Bedrock Guardrails flagged the blatantly poisoned article (`kb-004`) as a prompt attack, and the isolation layer withheld it.

**Still not verified:**

- That Bedrock Guardrails flags the *subtly* poisoned article (`kb-006`).
- Full scoreboard rates. Only the two refund attacks have been run against the deployment. Every number in the built-in sample scoreboard is invented.
- Nova Lite wraps its reasoning in `<thinking>` tags. The agent strips that before the customer (and the output guardrail) sees the reply. This was checked live on a few requests, not across the whole attack set.

## Limitations

- Small attack set written for this demo. Passing it says nothing about attacks outside it.
- The Guardrail uses the CLASSIC tier. The STANDARD tier needs cross-region inference, which is a data-residency decision to make deliberately.
- The data and side effects are simulated.
- The email policy rejects lists and stray separators, but it is a demonstration, not a complete exfiltration control.

## License

MIT, see [LICENSE](LICENSE).
