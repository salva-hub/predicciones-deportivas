#!/usr/bin/env bash
# Uso: publicar.sh "mensaje" ruta [ruta...]
# Hace commit de las rutas y las sube a main; reintenta pull --rebase + push hasta 3 veces.
set -euo pipefail
mensaje="$1"; shift
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add "$@"
if git diff --cached --quiet; then
  echo "Sin cambios"
  exit 0
fi
git commit -q -m "$mensaje"
for intento in 1 2 3; do
  if git pull --rebase -q origin main && git push -q origin HEAD:main; then
    exit 0
  fi
  echo "Push rechazado (intento $intento); reintentando..." >&2
  sleep $((intento * 10))
done
echo "No se pudo subir a main después de 3 intentos" >&2
exit 1
