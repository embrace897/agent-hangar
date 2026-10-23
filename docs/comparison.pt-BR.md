# Agent Hangar vs. plataformas de agentes: comparativo

Comparativo de 3 de outubro de 2026. English version: [comparison.md](comparison.md).

## Resumo

Nas fontes consultadas, o Agent Hangar é o único que junta, em um produto self-hosted, catálogo central de agentes criados em qualquer ferramenta, acesso por cinco protocolos (OpenAI-compatível, MCP, A2A, ACP e AG-UI para apps) e promoção para produção só depois de testes em stage. Os concorrentes se dividem em três grupos:

- **Frameworks com control plane comercial:** CrewAI AMP e LangSmith Deployment (LangGraph). Centralizam bem o que foi construído no próprio ecossistema.
- **Construtor visual:** Dify, forte em workflow e RAG, publica apps como API ou ferramenta MCP.
- **Clouds:** AWS Bedrock AgentCore e Microsoft Foundry com Agent 365. Têm registry e A2A, mas dentro do respectivo cloud.

O ponto de atenção é maturidade: o Agent Hangar está em alpha (v0.52) e o repositório não cita certificações de conformidade.

## Tabela comparativa

"Não encontrado" significa que a documentação consultada não descreve o recurso; não prova que ele não exista.

| Critério | Agent Hangar | CrewAI AMP | LangSmith Deployment | Dify | AWS Bedrock AgentCore | Microsoft Foundry + Agent 365 |
| --- | --- | --- | --- | --- | --- | --- |
| O que é | Control plane self-hosted para agentes de qualquer origem | Control plane comercial do framework CrewAI | Runtime de agentes (Agent Server) com observabilidade e avaliação | Plataforma visual de apps LLM, agentes e RAG | Serviços AWS: Runtime, Gateway e Registry | Serviço gerenciado Azure, com registry no Agent 365 |
| Hospedagem | Self-hosted: Docker e Kubernetes (GKE, AKS, EKS) | AMP Cloud ou AMP Factory (self-hosted em AWS, Azure ou GCP) | Cloud, híbrido ou self-hosted (plano Enterprise) | Cloud, VPC ou self-hosted (Community, Enterprise) | Nuvem AWS | Nuvem Azure |
| Modelos LLM que alimentam os agentes | Qualquer um, por conexões: OpenAI, Anthropic, Azure OpenAI, Gemini, OpenRouter, DeepSeek, Ollama, endpoint OpenAI-compatível e gateways (LiteLLM, Portkey) | Vários provedores via litellm, com integrações Azure e Bedrock | Definido pelo framework do agente; o runtime não prende a um provedor | Amplo; provedores instalados pelo Marketplace de plugins | Gateway dá acesso a LLMs; lista de modelos não verificada | Catálogo com mais de 10 mil modelos (OpenAI, Anthropic, Meta, DeepSeek e outros) |
| Preço e licença | Apache-2.0; sem custo de licença, você paga infraestrutura e tokens do LLM | Framework OSS; AMP com plano Basic gratuito e Enterprise sob consulta, cobrança por execução | Self-hosted exige plano Enterprise e chave de licença; valores não encontrados | Community gratuita; Cloud de US$ 59 a US$ 159 por workspace/mês; Enterprise sob consulta | Não verificado | Não verificado |
| Agentes feitos em Claude, ChatGPT, Cursor, Codex vão para um catálogo único | Sim, é o foco: a ferramenta cria o agente pelo MCP do hangar | Parcial: MCP server expõe operações de deploy do AMP a clientes como Claude | Parcial: faz deploy de LangGraph, Deep Agents e outros frameworks | Não encontrado | Sim, para MCP, A2A e agent cards sincronizados por URL (preview) | Parcial: agentes do Foundry, Copilot Studio e registrados por admin |
| Catálogo de agentes | Sim: dono, versão, pedido de acesso, playground | Sim: Agent Repositories e Marketplace | Não encontrado | Marketplace de plugins, não de agentes | Sim: Agent Registry, com aprovação manual opcional | Sim: Agent 365 registry e Entra Agent Registry |
| Acesso agnóstico a plataforma | OpenAI-compatível, MCP, A2A, ACP e AG-UI, por agente | A2A (0.2 e 0.3, early release) e API REST por crew | MCP e A2A nativos | API, web app, embed e ferramenta MCP | MCP via Gateway e A2A no Runtime | A2A 1.0, Responses e Activity; MCP via Toolbox |
| Portabilidade e lock-in | Baixo: spec em YAML com JSON Schema, GitOps (hangar apply), endpoints padrão e self-hosted | Médio: código do framework é portável; o deploy gerenciado é do AMP | Médio: LangGraph é aberto e há Agent Server standalone com Docker; plataforma completa é Enterprise | Médio: apps exportáveis como YAML DSL; self-hosted disponível | Protocolos abertos (MCP, A2A), mas runtime e Registry ficam na AWS | Aceita qualquer framework e A2A, mas hospedagem e registry ficam no Azure e no Microsoft 365 |
| Agentes combinados em novos agentes | Sim: sub\_agents via A2A, com checagem de permissão | Sim: crews, núcleo do framework | Sim: RemoteGraph via MCP e A2A | Sim: workflows com agentes encadeados | Sim: A2A entre agentes | Sim: ferramenta A2A |
| Catálogo de ferramentas | 230+ MCP servers do catálogo Docker; MCPs remotos com OAuth | Tool Repository e MCP servers próprios | Não encontrado | Marketplace com tools e integrações MCP | Gateway agrega APIs, Lambdas e MCPs num MCP virtual | Toolbox: endpoint MCP único, com governança e versão |
| Agentes de código e IDE | Harnesses (Claude Code, Codex, Hermes, DeepSeek Harness) como agentes; extensão de VS Code; Cline, Roo Code e Continue | Parcial: plugin de skills que ensina o CrewAI a agentes de código | Não encontrado | Não encontrado | Não encontrado | Não encontrado |
| Promoção com testes | Sim: versão imutável, gate de testes em stage, aprovação de segundo mantenedor, rollback | Não encontrado | Avaliação na mesma stack; gate de promoção não encontrado | Não encontrado | Não encontrado | Versiona agentes e cria endpoints estáveis; gate não encontrado |
| Memória de longo prazo | Grafo temporal por agente, time ou org (Graphiti), ou fatos vetoriais (Mem0, experimental) | Memória e knowledge no framework | Estado por thread, execução durável | Base de conhecimento (RAG) | Não verificado | Memory como ferramenta nativa |
| Governança e custo | SSO, SCIM, papéis, regra de e-mail corporativo, auditoria, DLP e retenção, orçamento por empresa, time, agente ou projeto com corte, custo por agente | RBAC e controles de segurança | Auth customizada; ABAC no self-hosted | SSO/SAML, RBAC e auditoria (Enterprise) | IAM/JWT e políticas Cedar no Gateway | Entra ID e controles do Agent 365 |
| Residência de dados e conformidade | Dados na sua infraestrutura, segredos criptografados, imagens assinadas com SBOM; sem certificações citadas | Implantação on-premise ou em nuvem; certificações não encontradas | Self-hosted para residência de dados e ambientes isolados (air-gapped) | Self-hosted mantém os dados em casa; Enterprise com SOC 2 Type II e ISO 27001 | Não verificado | Não verificado |
| Observabilidade | Prometheus, logs JSON, OpenTelemetry, métricas por canal | Observabilidade em tempo real | Ponto forte: traces e avaliação | Observabilidade integrada | AgentCore Observability e CloudWatch | Application Insights e telemetria do Agent 365 |
| Maturidade | Alpha (v0.52), Apache-2.0 | OSS com camada comercial | Comercial, parte Enterprise | Mais de 157 mil estrelas no GitHub | Registry em preview público | Agent 365 em disponibilidade geral; A2A 1.0 GA |

## Modelos LLM: o que cada plataforma permite

No Agent Hangar o modelo é um dado do agente, não da plataforma: cada agente aponta para uma conexão (URL, modelo e chave criptografada), que pode mudar por ambiente e registra custo em US$. Possibilidades de cada um:

- **Agent Hangar:** trocar o modelo de um agente sem recriá-lo; usar stage e produção com modelos diferentes; rodar modelo local com Ollama; chegar a Bedrock e Vertex por um gateway corporativo; usar harnesses de código (Claude Code, Codex, Hermes, DeepSeek Harness) como agentes.
- **CrewAI AMP:** escolher o modelo por agente no código, com suporte a vários provedores e integrações Azure e Bedrock registradas no changelog do framework.
- **LangSmith Deployment:** o modelo vem do framework do agente; o runtime hospeda o que o LangGraph ou outro framework já define, sem catálogo de modelos próprio nas fontes consultadas.
- **Dify:** instalar provedores de modelo pelo Marketplace e trocar o modelo de um app ou workflow no canvas.
- **AWS AgentCore:** o Gateway é descrito como ponto de acesso a ferramentas, outros agentes e LLMs; as fontes consultadas não listam os modelos suportados.
- **Microsoft Foundry:** escolher entre um [catálogo com mais de 10 mil modelos](https://atlan.com/know/ai-agent/microsoft/azure-ai-foundry/), com um único ponto de entrada para inferência e ferramentas.

## Onde o Agent Hangar se diferencia

O ponto central é que os agentes deixam de pertencer à ferramenta que os criou e passam a pertencer à empresa. Cinco pontos sustentam isso:

- **Constrói onde a pessoa já trabalha.** O hangar é um MCP server: Claude, ChatGPT, Codex ou OpenCode criam o agente, e Claude.ai e ChatGPT na web conectam só com a URL, via OAuth.
- **Nada vai a produção sem teste.** Cada mudança é uma versão imutável; a promoção exige testes de stage (smoke, regex e LLM-as-judge), com aprovação opcional de um segundo mantenedor e rollback em uma chamada.
- **Isolamento por agente.** Cada agente roda em container endurecido; harnesses de código (Claude Code, Codex) rodam em container novo por chamada e devolvem o resultado e o git diff.
- **Memória e custo como parte da plataforma.** Memória temporal por agente, time ou organização, e orçamento por empresa, time, agente ou projeto, com aviso e corte.
- **Traga seu próprio gateway de LLM.** O hangar não hospeda modelo; usa LiteLLM, OpenRouter ou provedor por conexões com chave criptografada.

## Onde os concorrentes ainda estão à frente

A comparação só é honesta se mostrar onde o Agent Hangar perde hoje:

- **Maturidade e conformidade.** O Dify Enterprise divulga SOC 2 Type II e ISO 27001, e a edição Community passa de 157 mil estrelas. O Agent Hangar está em alpha, e o README recomenda rodar dentro da rede até ler a documentação de segurança.
- **Observabilidade e avaliação.** O LangSmith tem traces e avaliação como produto central; o Agent Hangar entrega métricas, logs e OpenTelemetry, mas sem essa profundidade documentada.
- **Integração nativa ao cloud.** Agent 365 distribui agentes em Teams e Microsoft 365 Copilot; AgentCore aplica IAM e políticas Cedar no Gateway.
- **Construção visual.** O Dify tem canvas drag-and-drop; o Agent Hangar constrói por conversa, template ou YAML.
- **Roadmap em aberto.** Adaptadores de Slack e Teams e listas de egress para jobs ainda estão como próximos passos.

## Fontes

Baseado em trechos da documentação oficial retornados por busca em 03/10/2026, não nas páginas completas; confira os recursos marcados como preview antes de publicar.

- [Agent Hangar, README](https://github.com/valteresj2/agent-hangar)
- [CrewAI, A2A on AMP](https://docs.crewai.com/en/enterprise/features/a2a) · [repositório do CrewAI](https://github.com/crewAIInc/crewAI) · [resumo de terceiros do CrewAI Cloud](https://github.com/api-evangelist/crewai-cloud)
- [LangSmith Deployment](https://docs.langchain.com/langsmith/deployments) · [self-hosted](https://docs.langchain.com/langsmith/deploy-to-self-hosted-overview)
- [Dify](https://dify.ai/) · [análise de terceiros](https://aitoolradar.io/guides/dify)
- [AWS Agent Registry, conceitos](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-concepts.html) · [sincronização de registros](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-sync-records.html) · [AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-core-concepts.html)
- [Foundry Agent Service](https://github.com/MicrosoftDocs/azure-ai-docs/blob/main/articles/foundry/agents/overview.md) · [Agent 365 com Foundry](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-365-integration) · [Toolbox](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/tool-catalog)
