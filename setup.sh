#!/usr/bin/env bash
# System and Python dependencies for the catalog extractor.
set -euo pipefail

if ! command -v tesseract >/dev/null; then
  sudo apt-get update -q
  sudo apt-get install -y tesseract-ocr
fi

pip install -r "$(dirname "$0")/requirements.txt"
