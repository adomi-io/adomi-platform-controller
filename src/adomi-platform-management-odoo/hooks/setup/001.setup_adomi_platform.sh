#!/usr/bin/env bash
set -euo pipefail

ODOO_DB_NAME="${ODOO_DB_NAME:-odoo}"

echo "[hook_setup] Installing adomi_platform into ${ODOO_DB_NAME}..."

/usr/local/bin/wait-for-psql.py

odoo-bin \
  -c "${ODOO_CONFIG:-/volumes/config/_generated.conf}" \
  -d "${ODOO_DB_NAME}" \
  -i adomi_platform \
  -u adomi_platform \
  --without-demo=true \
  --stop-after-init

echo "[hook_setup] adomi_platform installed."
