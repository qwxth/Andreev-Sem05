#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# Выполнить один раз при наличии интернета, до подключения к сети ESP32.
docker compose -f docker-compose.yml pull postgres
docker compose -f docker-compose.yml build app
