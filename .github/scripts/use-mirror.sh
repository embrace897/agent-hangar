#!/bin/sh
# Escolhe, para cada imagem de .github/base-images.txt, a cópia do espelho no GHCR (quando existe) ou a origem
# (Docker Hub), e exporta a variável para os próximos passos do job (GITHUB_ENV). Precisa do login no ghcr.io antes.
set -u
MIRROR="ghcr.io/${GITHUB_REPOSITORY_OWNER:?}/hangar-mirror"
OUT="${GITHUB_ENV:-/dev/stdout}"
grep -v '^\s*#' "$(dirname "$0")/../base-images.txt" | while read -r var src tag; do
  [ -n "$var" ] || continue
  if docker buildx imagetools inspect "$MIRROR:$tag" >/dev/null 2>&1; then
    echo "$var=$MIRROR:$tag" >> "$OUT"
    echo "espelho: $var=$MIRROR:$tag"
  else
    echo "$var=${src#docker.io/library/}" >> "$OUT"
    echo "::warning::espelho $MIRROR:$tag indisponível; usando $src"
  fi
done
