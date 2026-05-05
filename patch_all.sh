# 1. Agregar Vault a Docker-Compose sin romper el archivo
sed -i '/  mailhog:/i \
  vault:\n\
    build:\n\
      context: ../vault\n\
      dockerfile: Dockerfile\n\
    container_name: mode_vault\n\
    environment:\n\
      DATABASE_URL:       "postgresql+psycopg2://postgres:${POSTGRES_PASSWORD:-postgres}@postgres:5432/modecissions"\n\
      INTERNAL_API_KEY:   ${INTERNAL_API_KEY:-modecissions-internal-key}\n\
    ports:\n\
      - "8300:8300"\n\
    volumes:\n\
      - ../vault/secrets.yaml:/vault/secrets.yaml:ro\n\
    depends_on:\n\
      - postgres\n' infra/docker-compose.yml

sed -i '/  mailhog:/i \
  vault:\n\
    image: modecissions/vault:latest\n\
    container_name: mode_vault\n\
    env_file: .env\n\
    environment:\n\
      DATABASE_URL:       "postgresql+psycopg2://postgres:${POSTGRES_PASSWORD}@postgres:5432/modecissions"\n\
      INTERNAL_API_KEY:   ${INTERNAL_API_KEY:-modecissions-internal-key}\n\
    ports:\n\
      - "8300:8300"\n\
    volumes:\n\
      - /opt/modecissions/vault/secrets.yaml:/vault/secrets.yaml:ro\n\
    networks:\n\
      - modecissions_net\n\
    depends_on:\n\
      - postgres\n\
    restart: unless-stopped\n\
    logging: *default-logging\n' infra/terraform/deploy/docker-compose.aws.yml
