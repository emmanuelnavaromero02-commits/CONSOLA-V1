#!/usr/bin/env bash
# v1.43.4 — Move ad-hoc .env backups out of the repo and into a
# locked-down per-user backup directory. Run after rotating secrets to keep
# the rotation trail accessible (read-only, owner-only) without polluting
# `git status`.
#
# Idempotent. Safe to run multiple times. Refuses to overwrite an existing
# backup destination.
set -euo pipefail

BACKUP_DIR="${OMEGA_ENV_BACKUP_DIR:-$HOME/.omega-env-backups}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

moved=0
shopt -s nullglob
for src in infra/.env.backup infra/.env.backup-* infra/.env.bak infra/.env.*.bak; do
    [ -f "$src" ] || continue
    base="$(basename "$src")"
    dest="$BACKUP_DIR/${base}-$(date +%s)"
    if [ -e "$dest" ]; then
        echo "[cleanup-env] refuse: $dest already exists" >&2
        continue
    fi
    cp -p "$src" "$dest"
    chmod 600 "$dest"
    rm -- "$src"
    echo "[cleanup-env] moved $src → $dest (chmod 600)"
    moved=$((moved + 1))
done

if [ "$moved" -eq 0 ]; then
    echo "[cleanup-env] no .env.backup files in infra/; nothing to do"
fi
