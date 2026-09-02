#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
UNIFIED_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

exec "${UNIFIED_ROOT}/install_workbench.sh" "$@"
