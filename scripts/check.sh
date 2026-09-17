#!/usr/bin/env bash
set -euo pipefail
platform_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$platform_root"
python3 scripts/check.py
