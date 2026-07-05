# LearnPulse

Track Azure feature changes by watching Microsoft Learn documentation.

Microsoft updates its public Learn documentation (sourced from
[MicrosoftDocs/azure-docs](https://github.com/MicrosoftDocs/azure-docs)) whenever a
service changes. That makes the docs repo a high-signal, machine-readable feed of
what's actually shipping across Azure — often ahead of blog posts and release notes.

**LearnPulse** turns that firehose of doc commits into:

1. **Per-service change tracking** — what changed in Azure Functions, AKS, App Service, … this week
2. **A summary view** — a digestible dashboard of meaningful changes, with noise (typos, link fixes, formatting) filtered out

See [PLAN.md](PLAN.md) for the full design and implementation plan.

## Status

🚧 Planning stage. The design plan lives in [PLAN.md](PLAN.md); work is tracked in
[Issues](../../issues).
