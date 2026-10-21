#!/bin/sh
# E2E do driver Kubernetes: sobe um k3s num container, importa as imagens locais, instala o chart com Helm e roda o smoke
# e o `hangar test-drive` contra o cluster (agentes como Deployments, jobs de harness como Jobs).
#   docker compose build central agent-runtime harness-base mcp-stdio-runner
#   pip install httpx pyyaml ./cli && sh tests/e2e/k8s.sh
# Precisa de Docker com containers privilegiados (o k3s). Apaga o k3s no fim.
set -u
K3S_IMAGE=${K3S_IMAGE:-rancher/k3s:v1.36.5-k3s1}
HELM_IMAGE=${HELM_IMAGE:-alpine/helm:3.19.0}
PORT=${K8S_E2E_PORT:-18090}
status=0
cleanup() { docker rm -f -v hangar-k3s >/dev/null 2>&1; rm -f /tmp/hangar-k3s.kubeconfig; }
trap cleanup EXIT
echo "== Kubernetes: k3s ($(date +%T))"
docker run -d --privileged --name hangar-k3s -p "$PORT:$PORT" "$K3S_IMAGE" server --disable traefik >/dev/null || exit 1
for i in $(seq 1 90); do docker exec hangar-k3s kubectl get nodes 2>/dev/null | grep -q " Ready" && break; sleep 3; done
for img in central agent-runtime harness-base mcp-stdio-runner; do  # o que o chart usa por padrão (Data Studio vem desligado)
  echo "   importando agent-hangar/$img"
  docker save "agent-hangar/$img:latest" | docker exec -i hangar-k3s ctr -n k8s.io images import - >/dev/null || { echo "   FAIL import $img"; exit 1; }
done
docker exec hangar-k3s cat /etc/rancher/k3s/k3s.yaml > /tmp/hangar-k3s.kubeconfig
echo "== Kubernetes: helm install ($(date +%T))"
if ! docker run --rm --network container:hangar-k3s -v /tmp/hangar-k3s.kubeconfig:/root/.kube/config:ro -v "$PWD:/w" -w /w "$HELM_IMAGE" \
     upgrade --install agent-hangar charts/agent-hangar -n agent-hangar --create-namespace \
     -f charts/agent-hangar/values-local.yaml --set image.registry=agent-hangar --set image.tag=latest \
     --set image.pullPolicy=IfNotPresent --wait --timeout 12m > /tmp/hangar-k3s-helm.log 2>&1; then
  echo "   FAIL helm"; tail -20 /tmp/hangar-k3s-helm.log
  docker exec hangar-k3s kubectl -n agent-hangar get pods
  docker exec hangar-k3s kubectl -n agent-hangar describe pods | grep -A6 "Events:" | tail -40
  exit 1
fi
echo "   ok   chart instalado"
docker exec -d hangar-k3s kubectl -n agent-hangar port-forward --address 0.0.0.0 svc/agent-hangar-central "$PORT:8080"
for i in $(seq 1 60); do curl -fs "http://localhost:$PORT/api/health" >/dev/null && break; sleep 2; done
curl -fs "http://localhost:$PORT/api/health" >/dev/null || { echo "   FAIL central não responde"; exit 1; }
TOKEN=$(docker exec hangar-k3s kubectl -n agent-hangar get secret agent-hangar-secrets -o jsonpath='{.data.ADMIN_TOKEN}' | base64 -d)
echo "== Kubernetes: smoke ($(date +%T))"
if HANGAR_URL="http://localhost:$PORT" HANGAR_TOKEN="$TOKEN" python tests/e2e/smoke.py > /tmp/hangar-k3s-smoke.log 2>&1; then
  echo "   PASSOU: $(grep -c '^ok' /tmp/hangar-k3s-smoke.log) verificações"
else
  status=1; echo "   FALHOU"; grep -E '^(FAIL|HTTP|Traceback)|Error' /tmp/hangar-k3s-smoke.log | head -8
  docker exec hangar-k3s kubectl -n agent-hangar get pods,jobs
fi
echo "== Kubernetes: hangar test-drive ($(date +%T))"
if HANGAR_URL="http://localhost:$PORT" HANGAR_TOKEN="$TOKEN" hangar test-drive > /tmp/hangar-k3s-td.log 2>&1; then
  echo "   ok   test-drive (contratação, experiência, admissão, tarefa) no cluster"
else
  status=1; echo "   FAIL test-drive"; tail -15 /tmp/hangar-k3s-td.log
fi
docker exec hangar-k3s kubectl -n agent-hangar logs deploy/agent-hangar-central --tail 2000 2>&1 | grep -E "ERROR|Traceback" | tail -10 || true
echo "== fim k8s ($(date +%T)), status=$status"
exit $status
