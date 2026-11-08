#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# Образы заранее подготовлены prepare.sh: на показе интернет не требуется.
exec docker compose -f docker-compose.yml up --no-build --pull never "$@"
