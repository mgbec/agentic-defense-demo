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
terraform plan                    # read it: about 46 resources
terraform apply
```

Region is `us-east-1` and the model is `amazon.nova-lite-v1:0`; both are Terraform variables.

The identity running Terraform needs more than the usual create permissions: `s3:GetObject` on the code bucket the config creates, and `bedrock-agentcore:InvokeGateway` on the guarded gateway. Policy creation validates the Cedar against the live gateway.

### 3. Attack it

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

## What is verified, and what is not

Verified locally (CI runs the same checks):

- Unit tests, including the Cedar policies run through a real Cedar engine (`cedarpy`), the oracles, the runner, the report and the web server.
- `terraform fmt`, `validate`, and a `plan` that adds 46 resources.
- The deployment zips are built for arm64 and contain the expected native libraries.
- Nova Lite was called live to confirm it makes a correct tool call, and attack behavior was measured with a local copy of the agent (not the deployed one).

**Not verified until the stack is deployed and run:**

- That AgentCore accepts the Cedar policies when it validates them against the live gateway.
- How a policy denial is surfaced to the agent. The hook recognizes common denial wording, so check the "gateway_policy" events on the first run.
- That the runtime accepts the zip and environment variables, and that the SigV4 MCP session initializes.
- That Bedrock Guardrails flags the subtly poisoned document.
- Real end-to-end success rates. Every number in the sample scoreboard is invented.

## Limitations

- Small attack set written for this demo. Passing it says nothing about attacks outside it.
- The Guardrail uses the CLASSIC tier. The STANDARD tier needs cross-region inference, which is a data-residency decision to make deliberately.
- The data and side effects are simulated.
- The email policy rejects lists and stray separators, but it is a demonstration, not a complete exfiltration control.

## License

MIT, see [LICENSE](LICENSE).
