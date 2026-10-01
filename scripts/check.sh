#!/usr/bin/env bash
set -euo pipefail
platform_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$platform_root"
python3 scripts/check.py
python3 scripts/check_schema_pin.py
python3 -m unittest discover -s tests -v
