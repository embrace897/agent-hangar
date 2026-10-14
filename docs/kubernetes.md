# Installing on Kubernetes (GKE, AKS, EKS)

Agent Hangar ships a Helm chart (`charts/agent-hangar`) and a Kubernetes runtime driver: each agent becomes a
`Deployment` + `Service`, each harness run or code evaluation becomes a `Job`. The same guided installer used for
Docker (`hangar setup`) drives the chart, so the questions and the result are the same: the LLM registered and
tested, the admin created and your AI tools configured.

## 1. Requirements

| | |
|---|---|
| Cluster | Kubernetes **1.27+** (GKE, AKS, EKS, or any conformant cluster: k3s, kind, Docker Desktop…) |
| Tools | `kubectl` pointing at the cluster (`kubectl config current-context`), **Helm 3.12+**, Python 3.10+ |
| Permissions | create a namespace and, inside it, Deployments, Jobs, Services, Secrets, Roles and NetworkPolicies |
| Nodes | 4 vCPU / 8 GB for the base install; each agent asks for up to 512 MiB / 1 vCPU (configurable) |
| Network | Outbound HTTPS to your LLM provider and to `ghcr.io` (or your mirror) |
| NetworkPolicy | a CNI that enforces it — see [the table below](#networkpolicy) |

## 2. Run the installer

```bash
git clone https://github.com/valteresj2/agent-hangar && cd agent-hangar
./scripts/install.sh --target kubernetes          # Windows: scripts\install.ps1 --target kubernetes
```

If the CLI is already installed, `hangar setup --target kubernetes` does the same. The installer asks:

| Question | Choices |
|---|---|
| Cloud / cluster | `gke` · `aks` · `eks` · `local` — picks the preset `values-<provider>.yaml` |
| Namespace and release | default `agent-hangar` / `agent-hangar` |
| Image registry | `ghcr.io/valteresj2` or your private mirror |
| Access | **Ingress with TLS** (host + cert-manager issuer when needed) · **Cloudflare Tunnel** (no load balancer) · port-forward only (testing) |
| Postgres | in the cluster (StatefulSet) · **managed** (Cloud SQL, Azure Database, RDS/Aurora) |
| Private egress | whether agents may reach private IPs (e.g. an LLM gateway inside your network) |
| Then | the same questions as Docker: admin, LLM, memory (Neo4j/FalkorDB), sign-in, code policy, AI tools |

What it does:
1. writes `.hangar/values.yaml` (choices, no secrets) and `.hangar/secrets.values.yaml` (mode 600, git-ignored);
2. runs `helm upgrade --install … --wait` with the preset of your cloud;
3. opens a temporary `kubectl port-forward` to the central and, through the API, registers and tests the LLM,
   creates the admin, and writes your AI tools' configs to `.hangar/clients/`;
4. prints the public address.

Unattended: `hangar setup --target kubernetes --answers setup.yaml` (see the `kubernetes:` block in
[`setup.example.yaml`](../setup.example.yaml)).

Re-running is safe: previous answers are the defaults and the generated secrets (`adminToken`, `secretKey`,
`internalSecret`, Postgres password) are reused. **Never change `secretKey` after installing**: it decrypts the LLM
keys stored in the database. Keep a copy of `.hangar/secrets.values.yaml` in your secret manager.

## 3. Check it

```bash
hangar doctor --namespace agent-hangar
```

It checks the pods (installation and agents), then everything `doctor` checks on Docker — central, admin, each LLM
with a real call, memory, the public address and TLS, sign-in, MCP and the VS Code extension — through a temporary
port-forward. Run from the same folder as the setup, `--namespace` is not needed.

## 4. Per cloud

### Google GKE

```bash
gcloud container clusters create-auto agent-hangar --region southamerica-east1   # Autopilot includes Dataplane V2
gcloud compute addresses create agent-hangar-ip --global                         # point your DNS host at it
```

- **Ingress:** GKE Ingress with a Google-managed certificate (`values-gke.yaml` creates the `ManagedCertificate`).
  Issuing the certificate takes 15–60 min after the DNS points to the IP.
- **Database:** Cloud SQL for PostgreSQL with a private IP in the cluster's VPC; answer *managed* and give
  `postgresql+psycopg://hangar:PASSWORD@PRIVATE_IP:5432/hangar`.
- **Vertex AI:** run a gateway (LiteLLM) with Workload Identity and register it as a *corporate gateway* connection.

### Azure AKS

```bash
az aks create -g rg-hangar -n agent-hangar --network-plugin azure --network-policy cilium --network-dataplane cilium
az aks approuting enable -g rg-hangar -n agent-hangar                             # managed NGINX ingress
```

- **TLS:** cert-manager with a `ClusterIssuer` (the installer asks for its name), or a Key Vault certificate via the
  `kubernetes.azure.com/tls-cert-keyvault-uri` annotation.
- **Database:** Azure Database for PostgreSQL – Flexible Server with private access;
  `...@<server>.postgres.database.azure.com:5432/hangar?sslmode=require`.
- **Azure OpenAI:** an OpenAI-compatible connection `https://<resource>.openai.azure.com/openai/v1` (model = deployment).

### Amazon EKS

```bash
eksctl create cluster -n agent-hangar --region sa-east-1 --with-oidc
# add-ons: AWS Load Balancer Controller, EBS CSI driver (gp3), VPC CNI with enableNetworkPolicy=true
```

- **Ingress:** ALB with an ACM certificate: set `alb.ingress.kubernetes.io/certificate-arn` in your values and point
  Route 53 to the ALB.
- **Database:** RDS / Aurora PostgreSQL in the same VPC; `...@<endpoint>.rds.amazonaws.com:5432/hangar?sslmode=require`.
- **Bedrock:** a gateway (LiteLLM) with IRSA or Pod Identity, registered as a *corporate gateway* connection.

### Other clusters (k3s, kind, Docker Desktop, on-premises)

Choose `local`. Use Ingress with your controller (`ingress.className`) and cert-manager, a Cloudflare Tunnel, or
port-forward only.

## 5. Security model

- **Agents and jobs** run as non-root (uid 10001), read-only root filesystem (agents), all capabilities dropped,
  `seccomp: RuntimeDefault`, no service-account token.
- **RBAC:** the central's ServiceAccount has a namespace-scoped `Role` (Deployments, Services, Jobs, Pods, logs,
  Secrets in its own namespace). Nothing cluster-wide.
- **Secrets:** agent environment (LLM tokens, connection keys) lives in a per-agent `Secret`; job secrets have an
  `ownerReference` to the Job and are deleted with it.

### NetworkPolicy

Agents, jobs and evaluations can reach only the central (port 8080), DNS and the internet **minus private ranges**
(10/8, 172.16/12, 192.168/16, 169.254/16, 100.64/10 — which blocks Postgres, memory and cloud metadata). Set
`networkPolicy.allowPrivateEgress=true` (installer question) if they must reach an internal LLM gateway.

Policies only take effect if the CNI enforces them:

| Cloud | How |
|---|---|
| GKE | Dataplane V2 (default on Autopilot; `--enable-dataplane-v2` on Standard) |
| AKS | `--network-policy azure`, `calico` or `cilium` |
| EKS | VPC CNI add-on with `enableNetworkPolicy=true`, or Calico/Cilium |
| Others | Calico, Cilium (k3s enforces them by default) |

### Analyst tools in the cluster (Data Studio and the governed browser)

Both are off by default; turn them on with `--set dataStudio.enabled=true --set browser.enabled=true`.

| | Data Studio | Governed browser |
|---|---|---|
| What runs | the MCP server; the analysis code runs in isolated processes inside its own pod (`SANDBOX=process`), with no Docker socket | Playwright, one session per task |
| Who reaches it | the central and the agents (MCP) | only the central |
| Egress | DNS and the internet, without private ranges | DNS and the internet, without private ranges |
| Storage | a PVC (`dataStudio.storage`, default 10Gi) for the session workspaces and deliverables | none (sessions live in memory) |
| Pod security | seccomp RuntimeDefault, no privilege escalation, every capability dropped except the ones it uses to switch uid per session (SETUID, SETGID, CHOWN, FOWNER, DAC_OVERRIDE, KILL) | non-root (uid 10002), seccomp RuntimeDefault, every capability dropped |

Data Studio downloads go through the central at `<publicUrl>/ds/f/…`. The link is signed by Data Studio, so it needs
no other port or ingress. With the tools off, the central turns those features off too. The governed desktop
(experimental), the Docker MCP catalog and Activepieces need the Docker socket and stay Compose-only.

### Idle stage environments

An agent's stage Deployment that has had no use for `agents.stageIdleMin` minutes (default 30) is scaled to 0. The
next test, probation task, shadow-mode task or stage playground call scales it back to 1. Production never scales
down. `0` turns it off. With several central replicas, use is shared through the database, so one replica never
scales down a stage another one is using. In Kubernetes the agent's `/data` is an `emptyDir`, so a hibernated stage
loses its temporary files (test conversations), and production keeps its own.

## 6. Air-gapped / private registry

Mirror the images (`central`, `agent-runtime`, `harness-*`, `mcp-memory`, and `mcp-data-studio`, `mcp-browser` and `desktop` if you use them) to your registry, answer its address in
the installer and, if it needs credentials, create a pull secret and set it in your values:

```yaml
image:
  registry: registry.acme.com/agent-hangar
  pullSecrets: [regcred]
```

## 7. Helm without the installer

```bash
helm upgrade --install agent-hangar charts/agent-hangar -n agent-hangar --create-namespace \
  -f charts/agent-hangar/values-gke.yaml -f my-values.yaml
```

The chart is also published as OCI with each release:
`helm install agent-hangar oci://ghcr.io/valteresj2/charts/agent-hangar --version <x.y.z>`.

The chart generates the secrets on the first install and keeps them on upgrades (the Secret has
`helm.sh/resource-policy: keep`); to manage them yourself, set `secrets.existingSecret`. All options are documented
in [`values.yaml`](../charts/agent-hangar/values.yaml). After installing, register the LLM and the admin in the console
(`/ui/`, sign in with the emergency token printed by `helm status`) or run `hangar setup --target kubernetes`.

## Secrets in a vault (External Secrets)

With the [External Secrets Operator](https://external-secrets.io), the chart stops generating secrets. The Secret
`<release>-secrets` then comes from your vault: AWS Secrets Manager, GCP Secret Manager, Azure Key Vault, HashiCorp
Vault, and others.

```yaml
externalSecrets:
  enabled: true
  secretStoreRef: {name: cofre-da-empresa, kind: ClusterSecretStore}
  remoteKey: agent-hangar/prod      # one vault secret (JSON) with every key below
  # apiVersion: external-secrets.io/v1beta1   # operators older than 0.17
```

Keys:
- **Always:** `ADMIN_TOKEN`, `HANGAR_SECRET_KEY` (Fernet), `INTERNAL_SECRET`, `DATABASE_URL`.
- **With the chart's Postgres:** `POSTGRES_PASSWORD`.
- **With memory:** `MEMORY_TOKEN` and `NEO4J_PASSWORD`/`FALKORDB_PASSWORD`.
- **With SSO:** `OAUTH_<PROVIDER>_CLIENT_SECRET`.
- **With the tunnel:** `CLOUDFLARE_TUNNEL_TOKEN`.

Use `data:` instead of `remoteKey` to map key by key. Rotating `HANGAR_SECRET_KEY` makes the stored LLM keys
unreadable: register them again after a rotation.

## Cloud identity (no keys in the cluster)

The central's ServiceAccount takes the cloud identity:

| Cloud | Values |
|---|---|
| GKE | `serviceAccount.annotations: {iam.gke.io/gcp-service-account: hangar@PROJECT.iam.gserviceaccount.com}` |
| EKS | `serviceAccount.annotations: {eks.amazonaws.com/role-arn: arn:aws:iam::ACCOUNT:role/agent-hangar}` |
| AKS | `serviceAccount.annotations: {azure.workload.identity/client-id: <id>}` and `central.podLabels: {azure.workload.identity/use: "true"}` |

On **GKE with Cloud SQL**, `postgresql.cloudSqlProxy` runs the Cloud SQL Auth Proxy next to the central and the backup
job: no IP allowlist, and with `iamAuth: true` no database password either. Then set
`postgresql.external.url: postgresql+psycopg://hangar:PASSWORD@127.0.0.1:5432/hangar`.

Agent pods never get the cloud identity (no ServiceAccount token).

## Observability and backup

- **Metrics:** each pod serves `:9100/metrics`. Set `observability.serviceMonitor.enabled` for the Prometheus Operator
  and `observability.grafanaDashboard.enabled` for the Grafana dashboard.
- **Logs:** JSON by default.
- **Traces:** OTLP with `observability.otlp.endpoint`.

See [observability.md](observability.md).

**Backup:** `backup.enabled` gives a daily `pg_dump` to a volume, plus one **before every `helm upgrade`**; if that
backup fails, the upgrade stops. See [backup.md](backup.md).

## Terraform

[`deploy/terraform`](../deploy/terraform/README.md) creates the whole stack in one `apply`: the cluster, managed Postgres
with HA and backups, cloud identity, ingress, and the chart with 2 replicas. Then run `hangar setup --configure-only`.

## High availability

Run two or more central replicas: answer the installer's *replicas* question, or set `central.replicas: 2` (or more).
- **Rolling updates** with no downtime (`maxUnavailable: 0`, a `preStop` pause so a pod leaves the Service before
  stopping), a **PodDisruptionBudget** (`central.pdb.minAvailable`), and replicas spread across nodes.
- **Shared state** lives in Postgres: job and evaluation callbacks, single-use codes (OAuth, VS Code), the login
  lockout and remote-MCP token refreshes work whichever replica receives them. Migrations run under a database lock,
  so replicas can start together.
- **Jobs** record the replica running them. A restarted replica recovers only its own jobs; jobs of a replica that
  disappears (no heartbeat for 75 s) are closed by the others within about a minute.
- Use a **managed Postgres with HA** (Cloud SQL, Azure Database, RDS/Aurora): the bundled StatefulSet is a single pod.

Validated on k3s with 2 replicas: concurrent start (one migration), harness jobs whose callbacks land on the other
replica, OAuth codes exchanged across replicas (single use), the login lockout shared, and a rolling restart under
continuous traffic with no failed request.

## Limits of this release

- Tested end to end on k3s, including External Secrets (fake provider), backups and metrics. The GKE/AKS/EKS
  presets, Cloud SQL Auth Proxy, cloud identity and Terraform follow each provider's documentation and pass
  `helm template`/`terraform validate`, but were not applied to real accounts by the project. Please report differences.
- Data Studio and the governed browser are part of the chart (see above). The governed desktop, the Docker MCP catalog
  and Activepieces need the Docker socket and stay Compose-only.
