#!/bin/bash
set -e
DEPLOY_DIR="/opt/modecissions/infra/terraform/deploy"
cd $DEPLOY_DIR

# Verificar que .env existe y tiene variables críticas
if [ ! -f .env ]; then
  echo "ERROR: .env no existe en $DEPLOY_DIR. Copia .env.example y complétalo."
  exit 1
fi

set -a
source .env
set +a

check_var() {
  if [ -z "${!1}" ]; then echo "ERROR: $1 no está definida en .env"; exit 1; fi
}

check_var POSTGRES_PASSWORD
check_var S3_BUCKET_NAME
check_var ANTHROPIC_API_KEY
check_var SUPERSET_SECRET_KEY
check_var AIRFLOW_SECRET_KEY

echo "✓ Variables de entorno OK"

# Postgres primero (necesita estar listo antes de los init containers)
echo "--- Iniciando Postgres ---"
docker compose -f docker-compose.aws.yml up -d postgres postgres_gold
echo "Esperando Postgres listo (30s)..."
sleep 30

# Init containers (DB superset/airflow ya creadas por init/*.sh del contenedor postgres)
echo "--- Iniciando init containers ---"
docker compose -f docker-compose.aws.yml up -d superset-init airflow-init
echo "Esperando init containers (60s)..."
sleep 60

# Levantar resto
echo "--- Iniciando todos los servicios ---"
docker compose -f docker-compose.aws.yml up -d

echo "--- Estado final ---"
docker compose -f docker-compose.aws.yml ps
