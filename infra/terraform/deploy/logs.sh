#!/bin/bash
cd /opt/modecissions/deploy
docker compose -f docker-compose.aws.yml logs -f ${1:-}
