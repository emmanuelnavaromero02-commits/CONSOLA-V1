FROM postgres:15.18-bookworm

COPY infra/init_gold/ /docker-entrypoint-initdb.d/
