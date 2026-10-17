# Roadmap

Priorities follow feedback: open an issue or 👍 the ones you care about. What each release delivered is in the
[CHANGELOG](CHANGELOG.md).

## Delivered

| Area | What is there today | Since |
|---|---|---|
| Lifecycle | Registry, immutable versions, stage tests as a gate to production, four-eyes approval, rollback, GitOps | v0.1 |
| Agent types | Chat, harness (Claude Code, Codex, Hermes, DeepSeek Harness), multi-agent over A2A, remote agents over AG-UI | v0.1, v0.40 |
| Protocols | OpenAI-compatible, MCP, A2A, ACP and AG-UI per agent, behind one authenticated gateway | v0.1, v0.38 |
| AG-UI | Agents inside your app (CopilotKit): streaming, reasoning, the app's tools, shared state, sub-agents, approvals as interrupts | v0.38–v0.39 |
| Images | Published to GHCR for amd64 and arm64, signed with cosign, with an SBOM | v0.12 |
| Teams and access | Teams and roles, SSO (OIDC, SAML), SCIM, several companies per installation, corporate e-mail rule | v0.3, v0.25, v0.37 |
| Memory | Temporal knowledge graph per agent, team or company | v0.6 |
| Kubernetes | Runtime driver, Helm chart, NetworkPolicies, high availability, Terraform for GKE, AKS and EKS | v0.10–v0.12 |
| Observability | Prometheus metrics, JSON logs, OpenTelemetry traces | v0.12 |
| Digital employees | Hiring, probation, authority per action type, decisions in the portal, Slack, Teams and e-mail, routines, teamwork | v0.16–v0.24 |
| Plugins and connectors | Declarative plugins with four-eyes review and an egress proxy; MCP connectors with tools approved one by one | v0.21–v0.24, v0.33–v0.35 |
| Cost and data | Budgets per company, team, agent or project with a hard stop; DLP, classification and retention; secrets vault | v0.25–v0.26 |
| Model use | Execution profile (effort, output limit, parallel tools, prompt cache), measured and compared per model | v0.36 |

## Next

- **Token streaming on the OpenAI-compatible endpoint** for chat agents (AG-UI already streams token by token).
- **A2A task lifecycle:** `tasks/get`, streaming and push notifications.
- **Egress allowlist for harness jobs and Data Studio kernels** (plugins with code already reach only their declared
  hosts).
- **Human approval for destructive harness actions**, like the authority of Digital employees.
- **Exportable audit trail** for a SIEM.
- **Remote agents over A2A**, besides AG-UI.
- **Live tests of the channels** against a real Slack workspace, a Teams tenant and an IMAP mailbox.
