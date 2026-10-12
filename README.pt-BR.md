<div align="center">

# ⌂ Agent Hangar

**Um lugar só para todos os agentes de IA da empresa, não importa em qual ferramenta foram criados.**

As pessoas criam agentes onde já trabalham: Claude, ChatGPT, Cursor, VS Code, Codex, OpenCode. O Agent Hangar
reúne todos num só lugar, roda cada um no seu container e deixa que sejam usados em **qualquer** plataforma, via
**OpenAI-compatible, MCP, A2A e ACP**. Todos podem encontrar os agentes, conversar com eles e combiná-los em
agentes novos.

[Demo interativa (inglês)](https://valteresj2.github.io/agent-hangar/demo/) · [English](README.md) · [Documentação](docs/) · [Roadmap](ROADMAP.md)

</div>

<p align="center"><img src="docs/assets/portal-tour.gif" width="900" alt="Tour do portal do usuário: a Ana pede ao
Claude um Assistente Comercial, que é criado, testado e publicado pelo MCP do Hangar; depois, a página Início, o
agente, o Playground, a memória do time, o resumo diário agendado, as conexões e o catálogo da empresa"></p>
<p align="center"><sub>O portal do usuário, gravado numa instalação local · <a href="docs/assets/portal-tour.mp4">versão MP4</a></sub></p>

> **Status: alpha (v0.28).** Funciona de ponta a ponta e tem testes, mas as APIs ainda podem mudar. Rode dentro da
> sua rede até ler [docs/security.md](docs/security.md).

## Por que o Agent Hangar

Hoje cada ferramenta de IA guarda os próprios agentes. Um GPT vive no ChatGPT, um projeto do Claude fica no Claude,
uma regra do Cursor fica no Cursor. Ninguém mais na empresa encontra esses agentes, eles não podem ser reusados em
outro lugar, e ninguém sabe quanto custam ou quem aprovou. O Agent Hangar resolve isso:

- **Crie em qualquer lugar, guarde num lugar só.** Peça um agente ao Claude, ChatGPT, Cursor, VS Code, Codex ou
  OpenCode. A ferramenta constrói o agente pelo MCP do hangar, e ele entra num catálogo único da empresa, com
  versões e um dono.
- **Agnóstico de plataforma.** Um agente criado no Claude pode ser usado no ChatGPT, Cursor, VS Code, LibreChat,
  Open WebUI, bots de chat, scripts ou por outro agente. Cada agente é um endpoint padrão (OpenAI-compatible, MCP,
  A2A, ACP), e não um recurso preso a um fornecedor.
- **Todos podem conhecer e conversar.** O catálogo mostra cada agente, o que ele faz e quem é o dono. As pessoas
  testam no playground do portal, pedem acesso e conectam na própria ferramenta em um clique. O acesso segue
  times, papéis e aprovações.
- **Agentes se combinam em novos agentes.** Junte agentes que já existem num time: um coordenador delega a
  especialistas (pesquisador, analista, redator), e cada um mantém as próprias instruções, ferramentas e modelo.
  O resultado é um agente novo, com outro papel, aproveitando o que já foi feito.
- **Reusar antes de construir.** Antes de criar um agente, o hangar procura no catálogo (busca semântica, em
  qualquer idioma) agentes, skills e MCPs que já fazem parte do trabalho. Ele monta o agente novo com essas peças e
  pergunta só as habilidades que faltam. As peças são só de leitura: nada nelas muda.
  [docs/composer.md](docs/composer.md)
- **Harnesses de código** (Claude Code, Codex, Hermes, DeepSeek Harness) rodam num container novo a cada chamada. As
  imagens do Codex, do Hermes e do DeepSeek são publicadas. A do Claude Code é construída localmente a partir deste
  repositório, porque a licença do Claude Code não permite redistribuí-lo; o instalador faz isso por você. Veja
  [docs/harnesses.md](docs/harnesses.md#the-claude-code-image-is-not-published).
- **Seguro para usar na empresa.**
  - Cada mudança vira uma versão nova, testada em stage antes de ir para produção.
  - Os segredos ficam criptografados, e tudo roda na sua infraestrutura (Docker ou Kubernetes).
  - Uso, custo e orçamento são registrados por agente e por time, com auditoria e SSO.
- **Agentes que lembram e trabalham sozinhos.** Memória de longo prazo por agente e por time, agendamentos para
  tarefas recorrentes, e agentes de código que editam projetos no VS Code.

Em resumo: **seus agentes deixam de pertencer a uma ferramenta e passam a pertencer à sua empresa.**

## Comparativo

Como o Agent Hangar se compara com CrewAI AMP, LangSmith Deployment, Dify, AWS Bedrock AgentCore e Microsoft Foundry
com Agent 365, em 3 de outubro de 2026. "Não encontrado" significa que a documentação consultada não descreve o
recurso; não prova que ele não exista. Onde cada um está à frente, os modelos LLM e as fontes:
[docs/comparison.pt-BR.md](docs/comparison.pt-BR.md).

| Critério | Agent Hangar | CrewAI AMP | LangSmith Deployment | Dify | AWS Bedrock AgentCore | Microsoft Foundry + Agent 365 |
| --- | --- | --- | --- | --- | --- | --- |
| O que é | Control plane self-hosted para agentes de qualquer origem | Control plane comercial do framework CrewAI | Runtime de agentes (Agent Server) com observabilidade e avaliação | Plataforma visual de apps LLM, agentes e RAG | Serviços AWS: Runtime, Gateway e Registry | Serviço gerenciado Azure, com registry no Agent 365 |
| Hospedagem | Self-hosted: Docker e Kubernetes (GKE, AKS, EKS) | AMP Cloud ou AMP Factory (self-hosted em AWS, Azure ou GCP) | Cloud, híbrido ou self-hosted (plano Enterprise) | Cloud, VPC ou self-hosted (Community, Enterprise) | Nuvem AWS | Nuvem Azure |
| Modelos LLM que alimentam os agentes | Qualquer um, por conexões: OpenAI, Anthropic, Azure OpenAI, Gemini, OpenRouter, DeepSeek, Ollama, endpoint OpenAI-compatível e gateways (LiteLLM, Portkey) | Vários provedores via litellm, com integrações Azure e Bedrock | Definido pelo framework do agente; o runtime não prende a um provedor | Amplo; provedores instalados pelo Marketplace de plugins | Gateway dá acesso a LLMs; lista de modelos não verificada | Catálogo com mais de 10 mil modelos (OpenAI, Anthropic, Meta, DeepSeek e outros) |
| Preço e licença | Apache-2.0; sem custo de licença, você paga infraestrutura e tokens do LLM | Framework OSS; AMP com plano Basic gratuito e Enterprise sob consulta, cobrança por execução | Self-hosted exige plano Enterprise e chave de licença; valores não encontrados | Community gratuita; Cloud de US$ 59 a US$ 159 por workspace/mês; Enterprise sob consulta | Não verificado | Não verificado |
| Agentes feitos em Claude, ChatGPT, Cursor, Codex vão para um catálogo único | Sim, é o foco: a ferramenta cria o agente pelo MCP do hangar | Parcial: MCP server expõe operações de deploy do AMP a clientes como Claude | Parcial: faz deploy de LangGraph, Deep Agents e outros frameworks | Não encontrado | Sim, para MCP, A2A e agent cards sincronizados por URL (preview) | Parcial: agentes do Foundry, Copilot Studio e registrados por admin |
| Catálogo de agentes | Sim: dono, versão, pedido de acesso, playground | Sim: Agent Repositories e Marketplace | Não encontrado | Marketplace de plugins, não de agentes | Sim: Agent Registry, com aprovação manual opcional | Sim: Agent 365 registry e Entra Agent Registry |
| Acesso agnóstico a plataforma | OpenAI-compatível, MCP, A2A e ACP, por agente | A2A (0.2 e 0.3, early release) e API REST por crew | MCP e A2A nativos | API, web app, embed e ferramenta MCP | MCP via Gateway e A2A no Runtime | A2A 1.0, Responses e Activity; MCP via Toolbox |
| Portabilidade e lock-in | Baixo: spec em YAML com JSON Schema, GitOps (hangar apply), endpoints padrão e self-hosted | Médio: código do framework é portável; o deploy gerenciado é do AMP | Médio: LangGraph é aberto e há Agent Server standalone com Docker; plataforma completa é Enterprise | Médio: apps exportáveis como YAML DSL; self-hosted disponível | Protocolos abertos (MCP, A2A), mas runtime e Registry ficam na AWS | Aceita qualquer framework e A2A, mas hospedagem e registry ficam no Azure e no Microsoft 365 |
| Agentes combinados em novos agentes | Sim: sub\_agents via A2A, com checagem de permissão | Sim: crews, núcleo do framework | Sim: RemoteGraph via MCP e A2A | Sim: workflows com agentes encadeados | Sim: A2A entre agentes | Sim: ferramenta A2A |
| Catálogo de ferramentas | 230+ MCP servers do catálogo Docker; MCPs remotos com OAuth | Tool Repository e MCP servers próprios | Não encontrado | Marketplace com tools e integrações MCP | Gateway agrega APIs, Lambdas e MCPs num MCP virtual | Toolbox: endpoint MCP único, com governança e versão |
| Agentes de código e IDE | Harnesses (Claude Code, Codex, Hermes, DeepSeek Harness) como agentes; extensão de VS Code; Cline, Roo Code e Continue | Parcial: plugin de skills que ensina o CrewAI a agentes de código | Não encontrado | Não encontrado | Não encontrado | Não encontrado |
| Promoção com testes | Sim: versão imutável, gate de testes em stage, aprovação de segundo mantenedor, rollback | Não encontrado | Avaliação na mesma stack; gate de promoção não encontrado | Não encontrado | Não encontrado | Versiona agentes e cria endpoints estáveis; gate não encontrado |
| Memória de longo prazo | Grafo temporal por agente, time ou org (Graphiti) | Memória e knowledge no framework | Estado por thread, execução durável | Base de conhecimento (RAG) | Não verificado | Memory como ferramenta nativa |
| Governança e custo | SSO, SCIM, papéis, auditoria, orçamento mensal por time com corte opcional, custo por agente | RBAC e controles de segurança | Auth customizada; ABAC no self-hosted | SSO/SAML, RBAC e auditoria (Enterprise) | IAM/JWT e políticas Cedar no Gateway | Entra ID e controles do Agent 365 |
| Residência de dados e conformidade | Dados na sua infraestrutura, segredos criptografados, imagens assinadas com SBOM; sem certificações citadas | Implantação on-premise ou em nuvem; certificações não encontradas | Self-hosted para residência de dados e ambientes isolados (air-gapped) | Self-hosted mantém os dados em casa; Enterprise com SOC 2 Type II e ISO 27001 | Não verificado | Não verificado |
| Observabilidade | Prometheus, logs JSON, OpenTelemetry, métricas por canal | Observabilidade em tempo real | Ponto forte: traces e avaliação | Observabilidade integrada | AgentCore Observability e CloudWatch | Application Insights e telemetria do Agent 365 |
| Maturidade | Alpha (v0.28), Apache-2.0 | OSS com camada comercial | Comercial, parte Enterprise | Mais de 157 mil estrelas no GitHub | Registry em preview público | Agent 365 em disponibilidade geral; A2A 1.0 GA |

## Exemplo: do pedido no Claude ao agente funcionando

Uma execução real, numa instalação local. A Ana é mantenedora do time Comercial e **não** é admin. Ela conectou o
Claude ao MCP do Hangar com o token pessoal dela e pediu:

> *"Crie no Agent Hangar um agente chamado “Assistente Comercial” para o nosso time acompanhar clientes (…). Use a
> conexão openrouter-deepseek, dê memória compartilhada com o time, inclua testes, publique em produção e agende
> para todo dia útil às 8h um resumo dos clientes com os próximos passos."*

**O Claude construiu o agente pelas ferramentas do MCP.**
1. `whoami` e `list_catalog`.
2. `register_agent`.
3. `design_agent`, com instruções, LLM, `memory: {scope: team}` e 3 testes com LLM como juiz.
4. `ship_agent`: stage, **9/9 verificações aprovadas** e produção, em 43 s.
5. `schedule_agent`: de segunda a sexta às 08:00.

**O time usou o agente.**
- A Ana registrou as novidades dos clientes, e o agente gravou cada uma na **memória do time**.
- Numa conversa nova, o agente respondeu quem precisa de contato na semana.
- O **resumo diário agendado** é o resultado final: a Beta Transportes é a mais urgente (o teste acaba em 15/10 e
  o CFO pediu proposta do Pro), seguida da Gama Foods (renovação em 10/11 e reclamação do suporte).

| Pedido no Claude | Início da Ana | Resumo diário agendado |
|---|---|---|
| ![Claude](docs/assets/portal/claude.png) | ![Início](docs/assets/portal/inicio.png) | ![Resumo](docs/assets/portal/resumo-agendado.png) |

Os detalhes estão no [README em inglês](README.md#from-a-request-in-claude-to-a-working-agent). Os scripts que
reproduzem a demo estão em [scripts/demo/portal-tour](scripts/demo/portal-tour/).

**Dois apps web:**
- **`/app/`** é o portal do usuário, para quem não é admin. Tem a página Início, os seus agentes, o catálogo, os
  pedidos, as chaves e os times.
- **`/ui/`** é o console de administração, só para admins.
- O login é o mesmo, e cada pessoa cai no lugar certo.
- Os dois apps estão em **português e inglês**. Na primeira visita vale o idioma do navegador; o seletor **PT · EN**
  (rodapé do menu e tela de login) troca, e a escolha fica guardada naquele navegador. O que pessoas e agentes
  escreveram (nomes, descrições, conversas, saídas de teste, auditoria) aparece como foi escrito. Mensagens geradas
  pelo servidor (erros da API, instruções do MCP, recomendação do composer) continuam em português.

## Instalação (passo a passo)

O instalador guiado faz as perguntas e cuida do resto: gera o `.env`, sobe a stack, cadastra e **testa** o LLM, cria o
admin e gera as configurações das ferramentas de IA. O guia completo está em [docs/install.md](docs/install.md).

1. **Pré-requisitos:** Docker (Desktop ou Engine) com Compose 2.20+, e Python 3.10+ para rodar o instalador. Funciona
   em Intel/AMD e ARM64 (Macs Apple Silicon, Graviton).
2. **Rode o instalador:**
   ```bash
   git clone https://github.com/valteresj2/agent-hangar && cd agent-hangar
   ./scripts/install.sh                                     # Linux, macOS, WSL
   # Windows: powershell -ExecutionPolicy Bypass -File scripts\install.ps1
   ```
3. **Responda às perguntas** (todas têm um padrão):
   - imagens publicadas ou build local;
   - endereço: local, **Cloudflare Tunnel**, **ngrok** ou **Tailscale Funnel**;
   - usuário e senha do admin;
   - LLM: OpenAI, Anthropic, Azure OpenAI, Gemini, OpenRouter, DeepSeek, gateway corporativo (LiteLLM, Portkey… e,
     por ele, Bedrock e Vertex), Ollama ou outro endpoint;
   - memória: Neo4j, FalkorDB ou nenhuma;
   - login: contas locais, Google, Entra ID, GitHub ou OAuth2;
   - extras e configurações para Claude Code, Claude Desktop, Codex, OpenCode, Cursor e VS Code.
4. **Abra o endereço** mostrado no fim e entre. As configurações das ferramentas de IA estão em `.hangar/clients/`
   (comece pelo `README.md` de lá).
5. **Confira com `hangar doctor`:** ele testa Docker, a central, cada LLM, a memória, o endereço público e o TLS, o
   login, o MCP e a extensão do VS Code, e diz como corrigir o que falhar.

### Numa VM na nuvem (AWS, Azure, Google Cloud)

Uma VM com Docker roda a mesma stack: crie uma VM Ubuntu 24.04 com SSH aberto só para você, instale o Docker
Engine, rode o instalador e escolha um túnel para o HTTPS (sem abrir portas). Os comandos passo a passo para EC2,
Azure Virtual Machines e Compute Engine, cada um com o link da documentação oficial do provedor, estão em
[docs/cloud-vm.md](docs/cloud-vm.md).

### No Kubernetes (GKE, AKS, EKS)

O mesmo instalador implanta o chart Helm (`charts/agent-hangar`): cada agente vira um Deployment e cada execução de
harness um Job, isolados por NetworkPolicies. Guia completo: [docs/kubernetes.md](docs/kubernetes.md).

1. **Pré-requisitos:** `kubectl` apontando para o cluster, Helm 3.12+ e Python 3.10+.
2. **Rode** `./scripts/install.sh --target kubernetes` (ou `hangar setup --target kubernetes`).
3. **Responda:** nuvem (`gke` · `aks` · `eks` · `local`, que escolhe o preset), namespace, registry das imagens (ou o
   seu espelho), acesso (**Ingress com TLS** · **Cloudflare Tunnel** · port-forward), Postgres (no cluster ou
   **gerenciado**: Cloud SQL, Azure Database, RDS) e as mesmas perguntas de admin, LLM, memória, login e ferramentas.
4. **Confira:** `hangar doctor --namespace agent-hangar`.

Para alta disponibilidade, use 2+ réplicas da central com Postgres gerenciado: atualização sem parada e estado
compartilhado no banco. Para criar tudo (cluster, Postgres gerenciado, identidade da nuvem, ingress) num
`terraform apply`, use [`deploy/terraform`](deploy/terraform/README.md) para GKE, AKS ou EKS.

Para instalar sem perguntas, use `./scripts/install.sh --answers setup.yaml`, com o
[`setup.example.yaml`](setup.example.yaml) como modelo. Para mudar alguma escolha ou atualizar, faça `git pull` e rode
`hangar setup` de novo: as respostas anteriores viram o padrão e os segredos são mantidos.

## O que faz

Você conecta o MCP do Agent Hangar ao cliente de IA que já usa e pede:

> *"Crie um agente que responde perguntas sobre nossa política de viagens, teste e coloque em produção."*

O cliente chama as ferramentas do hangar e faz o resto:

1. Registra o agente (nome, objetivo, saída esperada).
2. Escreve instruções, skills, tools e casos de teste.
3. Escreve o **guia** do agente para quem vai usá-lo (o que é, o que faz, como usar).
4. Faz deploy num container de **stage** e roda os testes.
5. Registra o resultado e só promove para **produção** se os testes passarem.

A partir daí o agente é um serviço com quatro endpoints padrão. Você pode plugá-lo no LibreChat, no Open WebUI, no
OpenCode, no Claude Desktop, num bot de Slack, em outro agente (via A2A) ou em qualquer SDK da OpenAI. Cada agente
também é **um servidor MCP próprio**, então as skills e tools dele continuam funcionando dentro de outras
ferramentas.

**Times e SSO:**
- Cada pessoa entra com Google, Microsoft Entra ID, GitHub ou outro provedor OAuth2.
- Cada agente pertence a um time, com os papéis mantenedor, developer e consumer.
- Produção exige a aprovação de outro mantenedor.
- Os outros times encontram agentes no catálogo da empresa e pedem acesso.
- O SCIM sincroniza usuários e grupos com o diretório.

Veja [docs/access.md](docs/access.md).

**Plug and play:** na aba **Conectar** do agente (ou `hangar connect <slug> <ferramenta>`) você escolhe a
ferramenta e o modo, e recebe uma chave só daquela ferramenta com a configuração pronta para colar. Toda ferramenta
pode usar o agente **via MCP** (Claude Code, Claude Desktop, Codex, OpenCode, Cursor, VS Code, LibreChat, Open WebUI);
as de chat também podem usá-lo **como modelo** (LibreChat, Open WebUI, OpenCode, SDKs OpenAI). Cada conexão é
opcional e pode ser revogada sozinha.

**Um guia para cada agente.** Cada agente tem a aba **Guia**, pensada para quem vai usá-lo:
- um fluxo montado a partir da spec (pedido, agente, as skills, ferramentas, memória e especialistas que ele usa, e
  o que ele entrega);
- uma ficha com pedidos de exemplo reais (os casos de teste aprovados) e onde chamar o agente;
- um texto em Markdown: o que é, o que faz, o que não faz, como usar e os limites.

Quem escreve o texto é a ferramenta de IA que criou o agente. Se uma versão vai para produção sem texto, o LLM do
agente escreve um rascunho, marcado para revisão até um mantenedor aprovar. Veja [docs/guide.md](docs/guide.md).

**Digital employees (agente como funcionário, com o humano no circuito).** Contrate um agente para um cargo em vez
de só chamá-lo. Ele tem cargo, gestor humano e **alçada**; recebe tarefas e trabalha nelas em segundo plano.
- **Contratação em modo self:** a sua ferramenta de IA pergunta pelo MCP o que faltar (`plan_employee` →
  `hire_employee`) e nada é criado até o cargo estar completo.
- **Experiência, depois admissão:** primeiro ele faz tarefas reais em stage; o gestor lê os resultados e o admite em
  produção.
- **Alçada aplicada pela plataforma, não pelo prompt:** antes de cada ferramenta, o runtime pergunta à central. Cada
  tipo de ação (ler, enviar para fora, dinheiro, apagar…) é feito sozinho, feito com aviso, aprovado por uma ou duas
  pessoas ou nunca feito, e o piso da empresa sempre vale.
- **Humano no circuito:** a tarefa pausa na ação exata (ferramenta e argumentos) e o gestor aprova, edita, recusa ou
  instrui. Sem resposta, o pedido vai para o substituto e depois expira sem a ação.
- **Onde gerenciar:** donos e gestores têm o seu espaço no portal (*Digital employees*, *Decisões*); o admin tem a
  força de trabalho, o catálogo de ações e o piso da empresa no console. Os relatórios diários podem ir para o Slack
  ou o Teams, o que for da empresa.
- **O dia a dia sozinho:**
  - rotinas por cron;
  - tarefas vindas de outros sistemas por um webhook de entrada, sem duplicar;
  - metas medidas contra o alvo num relatório semanal;
  - sugestões aprendidas com as decisões do gestor: afrouxar o que é sempre aprovado e transformar correções
    repetidas em lições.

Veja [docs/digital-employee.md](docs/digital-employee.md).

## Início rápido

```bash
git clone https://github.com/valteresj2/agent-hangar && cd agent-hangar
./scripts/setup.sh              # Windows: powershell -File scripts/setup.ps1  → gera o .env com segredos aleatórios
docker compose up -d --build
```

Abra **http://localhost:8090** e cole o `ADMIN_TOKEN` do `.env`. Em **Templates → Document Q&A**, clique em
**Aplicar + shipar**. Sem nenhuma chave, o agente roda em modo mock, o que basta para ver o fluxo completo. Depois
cadastre um provedor em **Provedores** (LiteLLM, OpenRouter, OpenAI, Anthropic, DeepSeek, Gemini, Ollama…).

Para conectar seu cliente, gere uma chave **admin** em *Chaves de API* e rode:

```bash
claude mcp add --transport http agent-hangar http://localhost:8090/mcp --header "Authorization: Bearer <chave>"
```

Harnesses de código são opcionais porque as imagens são grandes:
- `docker compose --profile harness build` para Claude Code, Codex e DeepSeek Harness;
- `docker compose --profile hermes build` para o Hermes.

## Por quê

- **Agentes são criados onde as pessoas já trabalham.** O hangar é um servidor MCP, então o próprio
  Claude/ChatGPT/Codex vira o "builder". Ele escolhe entre agente de chat, harness de código ou multiagente a partir
  do objetivo. No Claude.ai e no ChatGPT (web), basta colar a URL `/mcp`: a pessoa entra e autoriza (OAuth), sem
  copiar chave.
- **Nada chega à produção sem teste.** Cada mudança gera uma versão imutável, e a promoção exige que essa versão
  tenha passado nos testes de stage. Os testes combinam smoke dos protocolos, `expect_contains`/`regex` e
  **LLM-as-judge**. O rollback é um clique.
- **Um container por agente e um por job.** Agentes de chat rodam em containers endurecidos. Harnesses (Claude
  Code, Codex, Hermes, DeepSeek Harness) sobem **um container efêmero por chamada** e devolvem o resultado e o
  `git diff`.
- **Todos os protocolos.** Cada agente expõe OpenAI-compatible, **A2A**, **ACP** e **MCP** atrás de um gateway
  autenticado, com métricas por canal.
- **Use o seu gateway de LLM.** O hangar nunca hospeda modelo: os agentes chamam o seu LiteLLM, OpenRouter ou
  provedor por meio de *conexões*. As chaves ficam criptografadas, há override por ambiente e o custo é calculado
  em US$.
- **Governado como software.** Chaves com escopo (admin × invoke só de certos agentes), tokens internos por
  agente, auditoria, GitOps (`hangar apply -f agents.yaml`) e JSON Schema da spec.

## Data Studio + LibreChat

Exemplo completo de agente como **motor de uma interface de chat**:
- O LibreChat conversa com o agente `data-studio`.
- Cada conversa ganha um workspace próprio, com Python e DuckDB.
- Os anexos (planilhas, PDFs, imagens) são gravados lá automaticamente.
- O agente devolve dashboards HTML, apresentações PPTX/HTML, planilhas editadas e relatórios, como links.

Veja [integrations/librechat](integrations/librechat/).

## Documentação

[Conceitos](docs/concepts.md) · [Referência da spec](docs/spec.md) · [Harnesses](docs/harnesses.md) ·
[API](docs/api.md) · [CLI](docs/cli.md) · [Clientes](docs/clients.md) · [Templates](docs/templates.md) ·
[Segurança](docs/security.md) ·
[Digital employee](docs/digital-employee.md) · [Trabalho do dia a dia](docs/work.md) ·
[Mapa de funcionalidades](docs/feature-map.md)

## Licença

[Apache-2.0](LICENSE).
