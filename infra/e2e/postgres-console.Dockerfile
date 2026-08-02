FROM pgvector/pgvector:0.8.0-pg15

COPY infra/init/ /opt/omega-init/
RUN set -eu; \
    cp /opt/omega-init/00_schema.sql /docker-entrypoint-initdb.d/00_schema.sql; \
    cp /opt/omega-init/01_airflow_db.sh /docker-entrypoint-initdb.d/01_airflow_db.sh; \
    cp /opt/omega-init/02_replicon_seed.sql /docker-entrypoint-initdb.d/02_replicon_seed.sql; \
    cp /opt/omega-init/02_superset_db.sh /docker-entrypoint-initdb.d/02_superset_db.sh; \
    for source in $(find /opt/omega-init -maxdepth 1 -type f -name '*.sql' \
        ! -name '00_schema.sql' ! -name '02_replicon_seed.sql' | sort); do \
        printf '\n-- canonical source: %s\n' "$source"; \
        cat "$source"; \
    done > /docker-entrypoint-initdb.d/03_canonical_migrations.sql; \
    chmod 0555 /docker-entrypoint-initdb.d/01_airflow_db.sh \
        /docker-entrypoint-initdb.d/02_superset_db.sh; \
    chmod 0444 /docker-entrypoint-initdb.d/*.sql
