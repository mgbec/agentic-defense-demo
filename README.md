# Defense in depth for an AI agent

A small customer-support agent on AWS, with four switchable defense profiles and a red-team suite that shows what each layer stops. It is built to be run live in front of developers and security people.

The agent runs on Amazon Bedrock AgentCore. Its four tools (search a knowledge base, look up a customer, send an email, issue a refund) sit behind AgentCore Gateway. The tools are simulated: emails and refunds are recorded in a DynamoDB table and never sent or paid.

## The layers

Each profile adds one layer to the one before it.

| Profile | Adds | Good at | Blind to |
|---|---|---|---|
| `none` | Nothing. Model and prompt only. | - | Everything the model can be talked into |
| `guardrail` | Bedrock Guardrails on the user message and the reply (prompt-attack and content filters, SSN/card masking) | Obvious jailbreaks, PII leaking out | Text that looks harmless but carries intent |
| `isolation` | The same guardrail also screens retrieved documents; documents that pass are fenced as untrusted data; hardened system prompt | Instructions hidden in documents | Anything that is not in a document |
| `policy` | Cedar policy enforced by the gateway on every tool call | Over-limit refunds, email to outside domains. The model cannot argue with it | Harm inside what the policy allows |

No layer is complete. That is the point of stacking them.

![Four slices of cheese, each with holes in different places, then a plate of least-privilege tools. A red-team suite of attacks, controls and benign requests is scored against them.](docs/layers.png)

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
scripts/     build.py (deployment zips), mode.py (demo/lockdown switch), probe_model.py, make_sample_results.py
docs/        DEMO.md (presenter script), layers.svg and layers.png (the diagram above)
tests/       Unit tests; none need AWS credentials
```

## Quick start

**Everyone starts with Part 0.** Then pick what you want to do.

| I want to... | Do this |
|---|---|
| Set up my computer (everyone, once) | **Part 0** (about 5 minutes) |
| Look around without an AWS account | **Part A** |
| Put the agent in AWS | **Part B** (once) |
| Use the demo in my browser | **Part C** (each time) |

Part D (lockdown) and Part E (remove everything) are optional extras at the end.

**How to read the commands.** Every command is typed into a terminal that is open in the project folder (the folder that contains this README). On Windows, that is PowerShell. The commands start with `.venv\Scripts\python.exe`, which is the project's own copy of Python. Always use it, instead of plain `python`, because a global Python usually has an older `boto3` that cannot talk to AgentCore. On macOS or Linux, write `.venv/bin/python` instead.

### Part 0: Set up your computer (everyone, once)

Everything else in this README needs this part. If a folder named `.venv` already exists in the project, you have done it, so skip to the next part.

1. Check that you have Python 3.12 or newer.
   ```
   python --version
   ```
   It should print `Python 3.12` or higher. If it does not, install Python 3.12 first. This project was built and tested on Python 3.12. Newer versions have not been tested.
2. Create the project's own Python environment. This is the one command that uses plain `python`.
   ```
   python -m venv .venv
   ```
3. Install what the project needs into it. This takes a few minutes.
   ```
   .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```
4. Optional: check that everything works. It takes a few seconds and does not touch AWS.
   ```
   .venv\Scripts\python.exe -m pytest
   ```
   This runs the project's automated tests. A row of dots and a final line like `... passed in ... s` means success. An `F` means a test failed, and the details are printed below it. You never need to run this to use the demo.

### Part A: Look around without AWS

You do not need an AWS account for this part, but you must have done Part 0.

1. Start the web page in offline mode.
   ```
   .venv\Scripts\python.exe -m web --offline
   ```
2. Open http://localhost:8000/ in your browser.
3. Click the **Scoreboard** tab, choose **Sample data** in the "saved run" box and press **Open**.
4. Press Ctrl+C in the terminal to stop.

Offline mode shows invented sample data, and the page says so. It cannot send messages or run attacks, because there is no agent to talk to.

### Part B: Put the agent in AWS (once)

Do Part 0 first. You also need these installed: Terraform, [uv](https://docs.astral.sh/uv/), and the AWS CLI. You need AWS credentials for a **sandbox** account. Check that they work:

```
aws sts get-caller-identity
```

If that prints an account number, you are ready. Then run these four commands, in order:

```
.venv\Scripts\python.exe scripts\build.py
terraform -chdir=infra init
terraform -chdir=infra plan
terraform -chdir=infra apply
```

- `build.py` packages the agent and the four tools into the `dist` folder. It needs `uv`. It must run before `terraform`, because Terraform uploads those files.
- `plan` only shows what will be created. It changes nothing. Expect about 47 resources.
- `apply` creates everything. It shows the plan again and waits. Type `yes` to continue. It takes roughly 5 to 10 minutes.

When it finishes, check that it worked:

```
.venv\Scripts\python.exe scripts\mode.py status
```

It should print `Current mode: demo (weak profiles ON)`. That means all four defense profiles are available.

You do **not** repeat Part B each time you use the demo. The agent stays in AWS until you remove it (Part E).

Notes on Part B:
- Region is `us-east-1` and the model is `amazon.nova-lite-v1:0`. Both are Terraform variables.
- **Spending alert (recommended).** Create a file named `infra/terraform.tfvars` containing one line, `budget_alert_email = "you@example.com"`, before you run `plan`. Terraform then creates a $10 monthly budget that emails you. Git ignores that file, so your address stays out of the repository.
- The identity running Terraform needs more than the usual create permissions: `s3:GetObject` on the code bucket the config creates, `bedrock-agentcore:InvokeGateway` on the guarded gateway (policy creation validates against the live gateway), and `budgets:ModifyBudget` and `budgets:ViewBudget` if you use the spending alert.
- Terraform keeps its record of what it built in `infra/terraform.tfstate`. Keep that file, and run Part C from this same folder, because the web page reads it to find your agent.

### Part C: Use the demo (each time)

This needs Part 0 done and the agent deployed (Part B). The agent stays in AWS. This part starts the web page on your laptop, which talks to it.

1. Start the web page.
   ```
   .venv\Scripts\python.exe -m web
   ```
   The terminal prints `Demo page: http://localhost:8000/   [connected to AWS]`.
2. Open http://localhost:8000/ in your browser.
3. Use the page:
   - **Try it:** pick a defense profile card, then either choose a prepared message from the dropdown or type your own, and press **Send**. You see the reply, which layer stepped in, and what the tools actually did.
   - **Scoreboard:** press **Run** to attack all four profiles and see a table of results. The quick set is 96 runs and takes several minutes. You can also open a saved run.
   - **How it works:** a plain-language summary of the layers.
4. When you are done, press Ctrl+C in the terminal.

No browser? You can run the same attacks from the terminal:

```
.venv\Scripts\python.exe -m redteam list
.venv\Scripts\python.exe -m redteam run --quick
```

If something goes wrong:

| What you see | What it means | What to do |
|---|---|---|
| `boto3 ... is too old to know the bedrock-agentcore service` | You used a different Python | Use `.venv\Scripts\python.exe` exactly as written |
| `Nothing is deployed yet` | Terraform has no record of an agent | Do Part B, and run from the folder where you ran `apply` |
| `terraform is not installed` | The page cannot find Terraform | Install it, or set `DEMO_RUNTIME_ARN`, `DEMO_SINK_TABLE` and `DEMO_REGION` |
| The page says a profile is "switched off" | Lockdown is on | Run `.venv\Scripts\python.exe scripts\mode.py demo` (see Part D) |
| The port is busy | Something else is using port 8000 | Add `--port 8001` to the command and open `http://localhost:8001/` |
| An `Unable to locate credentials` or access error | Your AWS login is missing or expired | Sign in again, then retry |

### Part D: Lockdown (optional)

You do not need this. It is a switch on the agent that is already deployed. It is not a second deployment.

- **Demo mode** (the default): all four defense profiles work. This is what you want for a presentation.
- **Lockdown:** the agent serves only the fully defended `policy` profile and refuses the other three. Use it if other people or roles in your AWS account can call the agent.

```
.venv\Scripts\python.exe scripts\mode.py status
.venv\Scripts\python.exe scripts\mode.py lock
.venv\Scripts\python.exe scripts\mode.py demo
```

`lock` and `demo` each run `terraform apply` with one setting changed. Terraform shows a short plan and asks you to type `yes`. It changes a few settings on the existing agent, takes about a minute, and destroys nothing. Run `build.py` at least once beforehand, because Terraform compares the `dist` folder with what is deployed. A plain `terraform apply` always returns to demo mode, because that is the default.

### Part E: Remove everything

```
terraform -chdir=infra destroy
```

It lists what it will delete and asks you to type `yes`. It removes the agent, gateways, functions, database and budget. Run it when you are finished with the project. The stack costs little while idle but not nothing.

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

- **Lockdown** (`var.lockdown`, off by default) makes the deployment serve only the fully defended profile. The agent refuses the `none`, `guardrail` and `isolation` profiles, and the runtime role loses permission to call the unguarded `open` gateway. Switch it with `.venv\Scripts\python.exe scripts\mode.py lock` or `... demo` (see Part D).
- Lockdown gives little protection on its own. Calling the runtime already requires AWS IAM credentials with `bedrock-agentcore:InvokeAgentRuntime`, so outsiders are blocked either way, and anyone holding your admin credentials can switch it off. It only limits what other identities you have granted access to can do. It does not cap spend (use the budget alert), and it does not stop direct calls to the open gateway by an identity with `bedrock-agentcore:InvokeGateway`.
- The `open` gateway has no policy engine on purpose. In demo mode the runtime role can call it. The tools are simulated, so the worst outcome is rows in the sink table. Run `terraform destroy` when you finish.
- Both gateways use `exception_level = "DEBUG"`, which returns detailed errors to callers. Production gateways should not.
- The runtime uses `network_mode = "PUBLIC"`, so a hijacked agent could reach the internet. Its role is narrow and holds no secrets. For stricter setups, use VPC mode with limited egress.
- Inbound calls to the runtime require AWS (SigV4) credentials, because the Terraform configures no other authorizer. An unauthenticated request returned HTTP 403 on the live stack.
- Not hardened, because the data is fake: no TLS-only bucket policy, no DynamoDB point-in-time recovery or customer-managed key, and the broad AWS-managed Lambda logging policy.
- Reserved Lambda concurrency is not set. New accounts often have a low concurrency quota, and setting it can make the apply fail.

**If you fork or publish this repo**

- Turn on secret scanning and push protection.
- Add Dependabot for pip, Terraform and GitHub Actions. The agent's dependencies are pinned, so they will not update on their own.
- Pin the workflow actions to commit SHAs and require CI before merging to `main`.
- Never commit Terraform state or `*.tfvars`. Both are in `.gitignore`.

## What is verified, and what is not

Verified locally (CI runs the same checks):

- Unit tests (run them yourself in Part 0), including the Cedar policies run through a real Cedar engine (`cedarpy`), the oracles, the runner, the report and the web server.
- `terraform fmt`, `validate`, and a `plan` that adds 47 resources (48 with a budget).
- The deployment zips are built for arm64 and contain the expected native libraries.
- Nova Lite was called live to confirm it makes a correct tool call, and attack behavior was measured with a local copy of the agent (not the deployed one).

Verified on a real deployment (us-east-1, one run of the stack):

- AgentCore accepted all four Cedar policies when validating them against the live gateway.
- The runtime accepted the zip and environment variables, and the SigV4 MCP session works: the agent called tools through both gateways.
- A policy denial reaches the agent as "Tool call not allowed due to policy enforcement", and the `gateway_policy` event is recorded. In a small refund test (4 runs per cell) over-limit refunds went through 6 of 8 times with no defenses and 0 of 8 with the policy profile.
- Bedrock Guardrails flagged the blatantly poisoned article (`kb-004`) as a prompt attack, and the isolation layer withheld it.
- Nova Lite wraps its reasoning in `<thinking>` tags. The agent strips that before the customer (and the output guardrail) sees the reply, and live replies came back clean.
- Lockdown works in both directions on the live stack: with it on, the three weaker profiles are refused and `policy` still answers.
- A request with no AWS credentials to the runtime gets HTTP 403.
- The budget alert was created in AWS with the 80% actual and 100% forecast notifications.
- The side effect of making the isolation layer withhold `kb-004` is a cost: it is also the return-policy FAQ, so in `policy` mode the assistant may decline to quote the return policy.

**Still not verified:**

- That Bedrock Guardrails flags the *subtly* poisoned article (`kb-006`).
- Full scoreboard rates. Only the two refund attacks have been run against the deployment. Every number in the built-in sample scoreboard is invented.
- The `<thinking>` stripping across the whole attack set. It was checked live on a few requests only.

## Limitations

- Small attack set written for this demo. Passing it says nothing about attacks outside it.
- The Guardrail uses the CLASSIC tier. The STANDARD tier needs cross-region inference, which is a data-residency decision to make deliberately.
- The data and side effects are simulated.
- The email policy rejects lists and stray separators, but it is a demonstration, not a complete exfiltration control.

## License

MIT, see [LICENSE](LICENSE).
