#!/bin/bash
# Creates the superset database on the shared Postgres instance.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    SELECT 'CREATE DATABASE superset'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'superset')\gexec
EOSQL
