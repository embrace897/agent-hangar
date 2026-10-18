# Agent Hangar vs. agent platforms

Comparison as of October 3, 2026. Versão em português: [comparison.pt-BR.md](comparison.pt-BR.md).

## Summary

Among the sources consulted, Agent Hangar is the only one that combines three things in one self-hosted product:

- a central catalog of agents created in any tool;
- access through five protocols (OpenAI-compatible, MCP, A2A, ACP, and AG-UI for apps);
- promotion to production only after stage tests.

The other platforms fall into three groups:

- **Frameworks with a commercial control plane:** CrewAI AMP and LangSmith Deployment (LangGraph). They centralize
  well what was built in their own ecosystem.
- **Visual builder:** Dify. It is strong in workflows and RAG, and publishes apps as an API or an MCP tool.
- **Clouds:** AWS Bedrock AgentCore and Microsoft Foundry with Agent 365. They have a registry and A2A, but inside
  their own cloud.

Watch out for maturity: Agent Hangar is in alpha (v0.41), and the repository does not cite compliance certifications.

## Comparison table

"Not found" means the documentation consulted does not describe the feature; it does not prove the feature is missing.

| Criterion | Agent Hangar | CrewAI AMP | LangSmith Deployment | Dify | AWS Bedrock AgentCore | Microsoft Foundry + Agent 365 |
| --- | --- | --- | --- | --- | --- | --- |
| What it is | Self-hosted control plane for agents from any source | Commercial control plane of the CrewAI framework | Agent runtime (Agent Server) with observability and evaluation | Visual platform for LLM apps, agents and RAG | AWS services: Runtime, Gateway and Registry | Azure managed service, with the registry in Agent 365 |
| Hosting | Self-hosted: Docker and Kubernetes (GKE, AKS, EKS) | AMP Cloud or AMP Factory (self-hosted on AWS, Azure or GCP) | Cloud, hybrid or self-hosted (Enterprise plan) | Cloud, VPC or self-hosted (Community, Enterprise) | AWS cloud | Azure cloud |
| LLMs that power the agents | Any, through connections: OpenAI, Anthropic, Azure OpenAI, Gemini, OpenRouter, DeepSeek, Ollama, any OpenAI-compatible endpoint and gateways (LiteLLM, Portkey) | Several providers through litellm, with Azure and Bedrock integrations | Defined by the agent's framework; the runtime does not tie you to a provider | Broad; providers installed from the plugin Marketplace | The Gateway gives access to LLMs; model list not verified | Catalog of 10,000+ models (OpenAI, Anthropic, Meta, DeepSeek and others) |
| Price and license | Apache-2.0; no license cost, you pay for infrastructure and LLM tokens | OSS framework; AMP with a free Basic plan and Enterprise on request, billed per execution | Self-hosted requires the Enterprise plan and a license key; prices not found | Community is free; Cloud from US$59 to US$159 per workspace/month; Enterprise on request | Not verified | Not verified |
| Agents made in Claude, ChatGPT, Cursor, Codex go to one catalog | Yes, that is the focus: the tool creates the agent through the hangar's MCP | Partial: an MCP server exposes AMP deploy operations to clients such as Claude | Partial: deploys LangGraph, Deep Agents and other frameworks | Not found | Yes, for MCP, A2A and agent cards synced by URL (preview) | Partial: agents from Foundry, Copilot Studio and registered by an admin |
| Agent catalog | Yes: owner, version, access requests, playground | Yes: Agent Repositories and Marketplace | Not found | Plugin marketplace, not an agent catalog | Yes: Agent Registry, with optional manual approval | Yes: Agent 365 registry and Entra Agent Registry |
| Platform-agnostic access | OpenAI-compatible, MCP, A2A, ACP and AG-UI, per agent | A2A (0.2 and 0.3, early release) and a REST API per crew | Native MCP and A2A | API, web app, embed and MCP tool | MCP through the Gateway and A2A in the Runtime | A2A 1.0, Responses and Activity; MCP through the Toolbox |
| Portability and lock-in | Low: YAML spec with JSON Schema, GitOps (`hangar apply`), standard endpoints, self-hosted | Medium: framework code is portable; the managed deploy belongs to AMP | Medium: LangGraph is open and there is a standalone Agent Server with Docker; the full platform is Enterprise | Medium: apps export as YAML DSL; self-hosted available | Open protocols (MCP, A2A), but the runtime and Registry stay on AWS | Accepts any framework and A2A, but hosting and registry stay on Azure and Microsoft 365 |
| Agents combined into new agents | Yes: `sub_agents` over A2A, with permission checks | Yes: crews, the core of the framework | Yes: RemoteGraph through MCP and A2A | Yes: workflows with chained agents | Yes: A2A between agents | Yes: A2A tool |
| Tool catalog | 230+ MCP servers from the Docker catalog; remote MCPs with OAuth | Tool Repository and its own MCP servers | Not found | Marketplace with tools and MCP integrations | The Gateway aggregates APIs, Lambdas and MCPs into a virtual MCP | Toolbox: a single MCP endpoint, with governance and versioning |
| Coding agents and IDE | Harnesses (Claude Code, Codex, Hermes, DeepSeek Harness) as agents; VS Code extension; Cline, Roo Code and Continue | Partial: a skills plugin that teaches CrewAI to coding agents | Not found | Not found | Not found | Not found |
| Promotion gated by tests | Yes: immutable versions, stage test gate, approval by a second maintainer, rollback | Not found | Evaluation in the same stack; promotion gate not found | Not found | Not found | Versions agents and creates stable endpoints; gate not found |
| Long-term memory | Temporal graph per agent, team or org (Graphiti) | Memory and knowledge in the framework | Per-thread state, durable execution | Knowledge base (RAG) | Not verified | Memory as a native tool |
| Governance and cost | SSO, SCIM, roles, corporate e-mail rule, audit, DLP and retention, budgets per company, team, agent or project with a hard stop, cost per agent | RBAC and security controls | Custom auth; ABAC when self-hosted | SSO/SAML, RBAC and audit (Enterprise) | IAM/JWT and Cedar policies in the Gateway | Entra ID and Agent 365 controls |
| Data residency and compliance | Data on your infrastructure, encrypted secrets, signed images with SBOM; no certifications cited | On-premises or cloud deployment; certifications not found | Self-hosted for data residency and isolated (air-gapped) environments | Self-hosted keeps data in-house; Enterprise with SOC 2 Type II and ISO 27001 | Not verified | Not verified |
| Observability | Prometheus, JSON logs, OpenTelemetry, metrics per channel | Real-time observability | Strong point: traces and evaluation | Built-in observability | AgentCore Observability and CloudWatch | Application Insights and Agent 365 telemetry |
| Maturity | Alpha (v0.41), Apache-2.0 | OSS with a commercial layer | Commercial, partly Enterprise | 157,000+ GitHub stars | Registry in public preview | Agent 365 generally available; A2A 1.0 GA |

## LLMs: what each platform allows

In Agent Hangar the model belongs to the agent, not to the platform. Each agent points to a connection (URL, model and
encrypted key), which can change per environment and records cost in US$. What each platform makes possible:

- **Agent Hangar:**
  - switch an agent's model without recreating it;
  - use different models in stage and production;
  - run a local model with Ollama;
  - reach Bedrock and Vertex through a corporate gateway;
  - use coding harnesses (Claude Code, Codex, Hermes, DeepSeek Harness) as agents.
- **CrewAI AMP:** pick the model per agent in code, with several providers and the Azure and Bedrock integrations
  listed in the framework's changelog.
- **LangSmith Deployment:** the model comes from the agent's framework. The runtime hosts what LangGraph or another
  framework already defines; the sources consulted show no model catalog of its own.
- **Dify:** install model providers from the Marketplace and switch the model of an app or workflow on the canvas.
- **AWS AgentCore:** the Gateway is described as an access point to tools, other agents and LLMs; the sources
  consulted do not list the supported models.
- **Microsoft Foundry:** choose from a [catalog of 10,000+ models](https://atlan.com/know/ai-agent/microsoft/azure-ai-foundry/),
  with a single entry point for inference and tools.

## Where Agent Hangar stands apart

Agents stop belonging to the tool that created them and start belonging to the company. Five points support this:

- **It builds where people already work.** The hangar is an MCP server: Claude, ChatGPT, Codex or OpenCode create the
  agent. Claude.ai and ChatGPT on the web connect with just the URL, through OAuth.
- **Nothing reaches production untested.** Every change is an immutable version. Promotion requires stage tests
  (smoke, regex and LLM-as-judge), with optional approval by a second maintainer and a one-call rollback.
- **Isolation per agent.** Each agent runs in a hardened container. Coding harnesses (Claude Code, Codex) run in a
  fresh container per call and return the result and the git diff.
- **Memory and cost are part of the platform.** Temporal memory per agent, team or organization, and a monthly budget
  per team with an optional cutoff.
- **Bring your own LLM gateway.** The hangar hosts no model; it uses LiteLLM, OpenRouter or a provider through
  connections with encrypted keys.

## Where the others are still ahead

The comparison is only honest if it shows where Agent Hangar loses today:

- **Maturity and compliance.** Dify Enterprise advertises SOC 2 Type II and ISO 27001, and the Community edition has
  more than 157,000 stars. Agent Hangar is in alpha, and the README recommends running it inside your network until
  you have read the security documentation.
- **Observability and evaluation.** In LangSmith, traces and evaluation are the core product. Agent Hangar provides
  metrics, logs and OpenTelemetry, but without that documented depth.
- **Native cloud integration.**
  - Agent 365 distributes agents in Teams and Microsoft 365 Copilot.
  - AgentCore applies IAM and Cedar policies in the Gateway.
- **Visual building.** Dify has a drag-and-drop canvas; Agent Hangar builds through conversation, templates or YAML.
- **Open roadmap.** Slack and Teams adapters and egress allow-lists for jobs are still next steps.

## Sources

The comparison is based on excerpts of official documentation returned by searches on 2026-10-03, not on the full
pages. Check the features marked as preview before relying on them.

- [Agent Hangar, README](https://github.com/valteresj2/agent-hangar)
- CrewAI:
  - [A2A on AMP](https://docs.crewai.com/en/enterprise/features/a2a)
  - [CrewAI repository](https://github.com/crewAIInc/crewAI)
  - [third-party summary of CrewAI Cloud](https://github.com/api-evangelist/crewai-cloud)
- LangSmith:
  - [LangSmith Deployment](https://docs.langchain.com/langsmith/deployments)
  - [self-hosted](https://docs.langchain.com/langsmith/deploy-to-self-hosted-overview)
- Dify:
  - [Dify](https://dify.ai/)
  - [third-party review](https://aitoolradar.io/guides/dify)
- AWS:
  - [AWS Agent Registry, concepts](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-concepts.html)
  - [record sync](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-sync-records.html)
  - [AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-core-concepts.html)
- Microsoft:
  - [Foundry Agent Service](https://github.com/MicrosoftDocs/azure-ai-docs/blob/main/articles/foundry/agents/overview.md)
  - [Agent 365 with Foundry](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-365-integration)
  - [Toolbox](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/tool-catalog)
