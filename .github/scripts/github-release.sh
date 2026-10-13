#!/bin/sh
# Cria a release do GitHub para a tag (depois que as imagens e o chart foram publicados e assinados).
#   título: o assunto da tag anotada ("v0.23.0: plugin Creator mode…" -> "v0.23.0 — Plugin Creator mode…")
#   notas:  release-notes/<tag>.md, se existir (notas escritas à mão); senão a seção da versão no CHANGELOG.md
#   rodapé: imagens, chart, a extensão do VS Code anexada e o link para o CHANGELOG
# Se a release já existir (criada à mão), só anexa/atualiza a extensão.
set -eu
TAG="$1"
VERSION="${TAG#v}"
OWNER="${GITHUB_REPOSITORY_OWNER:?}"
REPO="${GITHUB_REPOSITORY:?}"
VSIX="extensions/vscode/agent-hangar-vscode.vsix"

SUBJECT=$(git tag -l --format='%(contents:subject)' "$TAG")
REST=$(printf '%s' "$SUBJECT" | sed -n "s/^$TAG:[[:space:]]*//p")
if [ -n "$REST" ]; then
  TITLE="$TAG — $(printf '%s' "$REST" | cut -c1 | tr '[:lower:]' '[:upper:]')$(printf '%s' "$REST" | cut -c2-)"
else
  TITLE="$TAG"
fi

HEADER=$(grep -m1 "^## \[$VERSION\]" CHANGELOG.md || true)
DATE=$(printf '%s' "$HEADER" | sed -n 's/.*— *\([0-9][0-9-]*\).*/\1/p')
ANCHOR="$(printf '%s' "$VERSION" | tr -d .)--$DATE"

if [ -f "release-notes/$TAG.md" ]; then
  cp "release-notes/$TAG.md" notes.md
else
  awk -v v="$VERSION" 'index($0, "## [" v "]") == 1 {f = 1; next} f && /^## \[/ {exit} f' CHANGELOG.md > notes.md
fi
if ! grep -q '[^[:space:]]' notes.md; then
  echo "::error::sem notas para $TAG: escreva a seção [$VERSION] no CHANGELOG.md ou release-notes/$TAG.md"
  exit 1
fi
cat >> notes.md <<EOF

### Images
Multi-arch (amd64 + arm64), signed with cosign: \`ghcr.io/$OWNER/<image>:$VERSION\` (central, agent-runtime, harness-*, mcp-*). Helm chart: \`oci://ghcr.io/$OWNER/charts/agent-hangar --version $VERSION\`.

The VS Code extension (\`agent-hangar-vscode.vsix\`) is attached.

Full changelog: [CHANGELOG.md](https://github.com/$REPO/blob/main/CHANGELOG.md#$ANCHOR)
EOF

if gh release view "$TAG" >/dev/null 2>&1; then
  gh release upload "$TAG" "$VSIX" --clobber
  echo "a release $TAG já existia: só a extensão foi anexada"
else
  gh release create "$TAG" --verify-tag --title "$TITLE" --notes-file notes.md "$VSIX"
  echo "release $TAG criada: $TITLE"
fi
