#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Arranque local del Intelligence System con UNA sola instrucción:
#
#     ./run.sh
#
# Hace, en orden:
#   1. Verifica el .env (lo crea desde .env.example si falta y avisa).
#   2. Crea el entorno virtual (Python 3.12) e instala las dependencias.
#   3. Levanta Redis Stack y Arize Phoenix en contenedores (si no están).
#   4. Ingesta la base de conocimiento en ChromaDB (si no hay índice).
#   5. Arranca la API en http://localhost:8000 (docs en /docs).
#
# Alternativa recomendada en una máquina limpia:  docker compose up --build
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")"

PY=python
command -v python3 >/dev/null 2>&1 && PY=python3

# 1. .env
if [ ! -f .env ]; then
  cp .env.example .env
  echo "AVISO: se creó .env desde .env.example. Completá la API key de UN proveedor y volvé a correr ./run.sh"
  exit 1
fi

# 2. Entorno virtual + dependencias
if [ ! -d .venv ]; then
  echo ">> Creando entorno virtual (.venv)…"
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.12 .venv
    VIRTUAL_ENV=.venv uv pip install -r requirements.txt
  else
    "$PY" -m venv .venv
    .venv/Scripts/pip -q install -r requirements.txt 2>/dev/null || .venv/bin/pip -q install -r requirements.txt
  fi
fi
VENV_PY=.venv/Scripts/python.exe
[ -x "$VENV_PY" ] || VENV_PY=.venv/bin/python

# 3. Redis Stack + Phoenix (requieren Docker). Redis STACK: trae RediSearch.
if command -v docker >/dev/null 2>&1; then
  docker ps --format '{{.Names}}' | grep -q '^is-redis$'   || docker run -d --name is-redis   -p 6379:6379 redis/redis-stack:7.4.0-v0 >/dev/null
  docker ps --format '{{.Names}}' | grep -q '^is-phoenix$' || docker run -d --name is-phoenix -p 6006:6006 arizephoenix/phoenix:version-7.9.0 >/dev/null
  echo ">> Redis (6379) y Phoenix (6006) arriba."
else
  echo "AVISO: Docker no está disponible; asegurate de tener Redis Stack y Phoenix corriendo a mano."
fi

# 4. Ingesta del RAG (idempotente: no reindexará si ya existe)
echo ">> Ingesta de la base de conocimiento…"
"$VENV_PY" -m app.rag.ingesta

# 5. API
echo ">> API en http://localhost:8000  (Swagger en /docs, Phoenix en http://localhost:6006)"
exec "$VENV_PY" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
