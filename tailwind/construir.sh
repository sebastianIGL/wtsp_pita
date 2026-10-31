#!/bin/sh
# Genera el CSS de Tailwind (se sube al repo; Railway no compila nada).
# Requiere el CLI standalone de Tailwind 3.4.17: https://github.com/tailwindlabs/tailwindcss/releases/tag/v3.4.17
# Uso:  TAILWIND_BIN=/ruta/a/tailwindcss ./tailwind/construir.sh     (o con `tailwindcss` en el PATH)
set -e
cd "$(dirname "$0")/.."
BIN="${TAILWIND_BIN:-tailwindcss}"
"$BIN" -c tailwind/tailwind.sitio.config.js -i tailwind/entrada.css -o frontend/css/sitio.css --minify
"$BIN" -c tailwind/tailwind.crm.config.js   -i tailwind/entrada.css -o frontend/css/crm.css   --minify
