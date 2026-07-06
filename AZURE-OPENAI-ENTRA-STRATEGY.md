# Using GPT-5.1 (Azure AI Foundry) with Entra ID — Auth & Secrets Strategy

**Status:** proposal · **Date:** 2026-07-06
**Scope:** replace/augment the Anthropic-key-based summarize stage (`pipeline/summarize.py`) with the
GPT-5.1 deployment in Azure AI Foundry, using Microsoft Entra ID only (API keys are disabled on the
resource), in a way that is safe for a **public repository** and works in **GitHub Actions**.

---

## 1. The big picture

API-key auth is disabled on the Foundry resource, so every caller must present an **Entra ID bearer
token** with an RBAC role on the resource. The good news: this is *more* secure and *easier* to run
in a public repo than a static key, because there is **no long-lived secret to leak at all**.

| Caller | How it authenticates | Long-lived secret stored anywhere? |
|---|---|---|
| You, locally | `az login` → `DefaultAzureCredential` | No |
| GitHub Actions | OIDC workload identity federation → `azure/login` | No |
| (Anti-pattern) | Service principal client secret in repo secrets | Yes — avoid |

The end state: GitHub's OIDC provider vouches for the workflow run; Entra ID exchanges that
short-lived OIDC token for an Azure access token (~1h lifetime); the pipeline calls the Foundry
endpoint with `Authorization: Bearer <token>`. Nothing in the repo, its secrets, or the workflow
logs can be stolen and replayed from outside GitHub Actions.

```
GitHub Actions run ──OIDC token──▶ Entra ID ──access token──▶ Foundry GPT-5.1 endpoint
   (id-token: write)     (federated credential trust,           (RBAC: Cognitive Services
                          subject-locked to this repo)           OpenAI User on this resource only)
```

---

## 2. Entra ID auth against the Foundry resource — how it works

### Requirements on the Azure side

1. **Custom subdomain endpoint.** Token auth requires the resource to have a custom subdomain
   (`https://<resource-name>.openai.azure.com` or `<resource-name>.services.ai.azure.com`), not a
   regional shared endpoint. Resources created via the Foundry portal already have one.
2. **RBAC role on the resource.** The least-privileged built-in role for inference is
   **Cognitive Services OpenAI User** (role ID `5e0bd9bd-7b93-4f28-af87-19fc36ad61bd`).
   Assign it **scoped to the single Foundry resource**, not the resource group or subscription.
   Role assignments take up to ~5 minutes to propagate.
3. **Token scope.** Request tokens for `https://ai.azure.com/.default` (current Foundry guidance;
   the legacy `https://cognitiveservices.azure.com/.default` scope also works against
   `*.openai.azure.com` endpoints).

### The v1 API (recommended)

The **v1 API** (`https://<resource>.openai.azure.com/openai/v1/`) removes the dated `api-version`
parameter and lets the standard `openai` Python client handle Entra tokens with automatic refresh —
no `AzureOpenAI` client needed:

```python
from openai import OpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), "https://ai.azure.com/.default"
)

client = OpenAI(
    base_url="https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1/",
    api_key=token_provider,   # callable → fetched & refreshed automatically
)
```

`DefaultAzureCredential` resolves, in order, environment credentials → workload identity (what
`azure/login` sets up in Actions) → managed identity → Azure CLI (`az login`, what you use locally).
The same code therefore works unchanged on your laptop and in CI.

### GPT-5.1 specifics for the summarize use case

- `gpt-5.1` (2025-11-13) supports both **Chat Completions** and the **Responses API**; 400k context
  (272k in / 128k out).
- `reasoning_effort` **defaults to `none`** on gpt-5.1 — fast and cheap, which is exactly right for
  short classification/summary calls. Set `low`/`medium` only if quality demands it.
- Use `max_completion_tokens` (Chat Completions) or `max_output_tokens` (Responses API) —
  `max_tokens` is not supported. `temperature` is likewise not supported on GPT-5-series models.
- **Structured Outputs are supported** — use a JSON schema instead of the current
  "respond with ONLY a JSON object" prompt + regex parsing. This eliminates most parse failures:

```python
completion = client.chat.completions.create(
    model="gpt-5.1",             # your *deployment* name
    messages=[{"role": "user", "content": prompt}],
    max_completion_tokens=300,
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "doc_change",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": [
                        "new-feature", "ga", "preview", "deprecation",
                        "breaking-change", "doc-update"]},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["kind", "title", "summary"],
                "additionalProperties": False,
            },
        },
    },
)
```

---

## 3. GitHub Actions: OIDC federation (no secrets)

### 3.1 Identity choice: user-assigned managed identity (recommended)

Federate against a **user-assigned managed identity (UAMI)** rather than an app registration.
A UAMI **cannot have client secrets or certificates added to it**, so there is nothing to leak even
by mistake — the *only* way to act as this identity is via the federated trust. An app registration
works identically but leaves the door open for someone to later mint a client secret on it.

### 3.2 One-time Azure setup

```bash
RG=<resource-group>
FOUNDRY=<foundry-resource-name>
SUB=$(az account show --query id -o tsv)

# 1. Identity for the pipeline
az identity create -g "$RG" -n learnpulse-pipeline
CLIENT_ID=$(az identity show -g "$RG" -n learnpulse-pipeline --query clientId -o tsv)
PRINCIPAL_ID=$(az identity show -g "$RG" -n learnpulse-pipeline --query principalId -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)

# 2. Trust GitHub Actions OIDC tokens — locked to this repo AND the 'pipeline' environment
az identity federated-credential create \
  --identity-name learnpulse-pipeline -g "$RG" \
  --name learnpulse-gha \
  --issuer  https://token.actions.githubusercontent.com \
  --subject repo:nthewara/LearnPulse:environment:pipeline \
  --audiences api://AzureADTokenExchange

# 3. Least-privilege role, scoped to the ONE resource
az role assignment create \
  --assignee-object-id "$PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Cognitive Services OpenAI User" \
  --scope "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$FOUNDRY"

# 4. Your own account, for local dev
az role assignment create \
  --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --role "Cognitive Services OpenAI User" \
  --scope "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$FOUNDRY"
```

The **subject claim is the security boundary**. `repo:nthewara/LearnPulse:environment:pipeline`
means only workflow runs *in this exact repo* that target the `pipeline` environment can redeem a
token. Forks have a different `repo:` owner, so their OIDC tokens can never match — even if someone
copies the workflow file verbatim.

### 3.3 GitHub repo setup

1. Create an **environment** named `pipeline` (Settings → Environments) and restrict its
   **deployment branches** to `main`. Optionally add a required reviewer for defense in depth.
2. Store as **environment secrets** (they aren't cryptographic secrets — client/tenant/subscription
   IDs are identifiers — but storing them as secrets keeps them out of logs and PRs):
   - `AZURE_CLIENT_ID` — the UAMI client ID
   - `AZURE_TENANT_ID`
   - `AZURE_SUBSCRIPTION_ID`
3. Store the endpoint as a repo **variable**: `AZURE_OPENAI_ENDPOINT = https://<resource>.openai.azure.com`
   (not sensitive; keys are disabled and RBAC gates access).

### 3.4 Workflow changes (`.github/workflows/pipeline.yml`)

```yaml
permissions:
  contents: write
  pull-requests: write
  id-token: write          # required for OIDC

jobs:
  run:
    runs-on: ubuntu-latest
    environment: pipeline   # must match the federated credential subject
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install pyyaml azure-identity openai

      - name: Azure login (OIDC, no secrets)
        uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}

      - name: Run pipeline
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          AZURE_OPENAI_ENDPOINT: ${{ vars.AZURE_OPENAI_ENDPOINT }}
          AZURE_OPENAI_DEPLOYMENT: gpt-5.1
        run: python pipeline/run.py
```

After `azure/login`, `DefaultAzureCredential` in the Python process picks up the Azure CLI /
workload-identity credential automatically — no token plumbing in env vars, and tokens refresh
themselves on long runs.

### 3.5 Code changes (`pipeline/summarize.py`)

Keep the existing contract — *the summarize stage must never fail the pipeline* — and swap the
transport:

```python
import os

def _make_client():
    """Return an OpenAI client for the Foundry endpoint, or None (heuristic fallback)."""
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if not endpoint:
        return None
    try:
        from openai import OpenAI
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(), "https://ai.azure.com/.default")
        return OpenAI(base_url=endpoint.rstrip("/") + "/openai/v1/",
                      api_key=token_provider)
    except Exception:
        return None  # missing deps / no credential — fall back to heuristics
```

Preserve the existing behaviors: 60s timeout, circuit breaker after repeated failures, strict
validation of the returned JSON (`kind` in the allowlist, non-empty title/summary), heuristic
fallback on any error. If `AZURE_OPENAI_ENDPOINT` is unset and `ANTHROPIC_API_KEY` is set, the
current Anthropic path can remain as a fallback during migration.

### 3.6 Local development

```bash
az login                      # once
export AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
python pipeline/summarize.py
```

`DefaultAzureCredential` finds the CLI login; no keys, no `.env` files with secrets.

---

## 4. Public-repo threat model & mitigations

| Threat | Mitigation |
|---|---|
| Secret committed to the repo | **There is no secret.** Client/tenant IDs are non-sensitive identifiers; the API key is disabled at the resource. Still enable GitHub **secret scanning + push protection** for the remaining `GITHUB_TOKEN`/misc hygiene. |
| Fork opens a PR that runs the LLM step | `pull_request` runs from forks get **no secrets and no `id-token`**. The pipeline is `workflow_dispatch`-only today; keep LLM steps out of any PR-triggered workflow. **Never** use `pull_request_target` with a checkout of PR head code. |
| Someone copies the workflow into their own repo | Their OIDC token's `sub` claim says `repo:their-org/their-repo:...` — the federated credential only trusts `repo:nthewara/LearnPulse:environment:pipeline`. Token exchange fails. |
| Malicious workflow change via PR | The `pipeline` environment is restricted to `main`, and main is protected by the ruleset (PR required). A PR can *propose* a workflow change but can't run it against the environment until merged by you. |
| Compromised identity blast radius | Role is `Cognitive Services OpenAI User` (inference only — can't read keys, can't manage the resource, can't touch anything else), scoped to one resource. Revocation = delete one role assignment or the federated credential; tokens expire in ~1h anyway. |
| Prompt injection via commit diffs (untrusted input) | Already partially handled: output is parsed as strict JSON with an enum allowlist and length caps, and is only rendered as text in the dashboard. Structured Outputs (§2) tightens this further. Keep treating model output as data, never as commands/URLs to act on. |
| Runaway spend / abuse | Set the deployment's **TPM/RPM capacity** low (summaries are tiny), add an Azure **budget alert** on the resource group, and keep the existing circuit breaker. Entra sign-in logs + resource metrics give per-identity audit. |
| Supply-chain (actions) | Optionally pin third-party actions (`azure/login`) to a full commit SHA. |

**Rules of thumb going forward**

1. Nothing that grants access to Azure lives in the repo or in repo secrets — only *identifiers*.
2. Any new workflow that needs Azure must use `environment: pipeline` + OIDC; never add a client
   secret, and never widen the federated credential subject (no `repo:...:*` wildcards).
3. RBAC assignments stay at single-resource scope with the narrowest built-in role.
4. LLM calls only run on `main` (scheduled/dispatch), never on untrusted PR events.

---

## 5. Rollout plan

1. **Azure setup** (§3.2) — UAMI, federated credential, role assignments. ~10 minutes.
2. **Local smoke test** — `az login`, set `AZURE_OPENAI_ENDPOINT`, call the deployment once
   (`client.chat.completions.create(model="gpt-5.1", ...)`). Confirms RBAC + endpoint before
   touching CI.
3. **Code** — add the Azure client path to `summarize.py` behind `AZURE_OPENAI_ENDPOINT`, keeping
   heuristic fallback and (temporarily) the Anthropic path.
4. **Workflow** — add `id-token: write`, `environment: pipeline`, `azure/login`, new env vars.
5. **Dry run** — `workflow_dispatch` with a small `since_days`; check the run log for
   `llm=<n> heuristic=<m>` counters.
6. **Cleanup** — delete the `ANTHROPIC_API_KEY` repo secret and remove the Anthropic code path once
   GPT-5.1 output quality is confirmed.

### Troubleshooting quick reference

| Symptom | Likely cause |
|---|---|
| `401 Unauthorized` | Wrong token scope — use `https://ai.azure.com/.default`; or endpoint isn't the custom subdomain. |
| `403 Forbidden` | Role not assigned, wrong scope, or <5 min propagation delay. |
| `AADSTS70021: No matching federated identity record` | Federated credential `subject` doesn't exactly match — job must run with `environment: pipeline` in `nthewara/LearnPulse`. |
| `azure/login` fails with OIDC error | Missing `id-token: write` permission in the workflow/job. |
| Works locally, fails in CI | Local `az login` user has the role but the UAMI doesn't (or vice versa) — they are separate principals. |

---

## Sources

- [Azure OpenAI in Microsoft Foundry Models v1 API](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle) — v1 base URL, Entra token provider code, scope
- [Configure keyless authentication with Microsoft Entra ID](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/configure-entra-id) — DefaultAzureCredential guidance, disabling key auth
- [Authentication and authorization in Microsoft Foundry](https://learn.microsoft.com/azure/foundry/concepts/authentication-authorization-foundry) — feature matrix, troubleshooting, custom subdomain requirement
- [How to configure Azure OpenAI with Microsoft Entra ID](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/managed-identity) — Cognitive Services OpenAI User role
- [Workload identity federation concepts](https://learn.microsoft.com/entra/workload-id/workload-identity-federation) — OIDC trust model
- [Configure federated credential on a user-assigned managed identity](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-create-trust-user-assigned-managed-identity)
- [Azure OpenAI reasoning models](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/reasoning) — gpt-5.1 `reasoning_effort` default `none`, `max_completion_tokens`, structured outputs
- [Foundry Models sold by Azure](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure) — gpt-5.1 (2025-11-13) capabilities and limits
