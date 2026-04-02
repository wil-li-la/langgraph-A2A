#!/usr/bin/env bash
# Sets up the Python virtual environment for the LangGraph A2A backend.
# Run from the backend/ directory: bash setup.sh
set -euo pipefail

PYTHON=${PYTHON:-python3.12}

echo "==> Creating virtual environment..."
$PYTHON -m venv .venv

echo "==> Installing stretch3-zmq-core (ZMQ client, LFS skipped)..."
GIT_LFS_SKIP_SMUDGE=1 git clone https://github.com/lnfu/stretch3-zmq.git /tmp/stretch3-zmq
.venv/bin/pip install /tmp/stretch3-zmq/packages/core

echo "==> Installing cure skills (no-detection branch, LFS skipped)..."
GIT_LFS_SKIP_SMUDGE=1 git clone --branch no-detection https://github.com/lnfu/cure.git /tmp/cure-no-detection
.venv/bin/pip install --no-deps /tmp/cure-no-detection

echo "==> Installing cure runtime dependencies..."
.venv/bin/pip install \
  rerun-sdk \
  pyzmq \
  scipy

echo "==> Installing project dependencies (pyproject.toml)..."
.venv/bin/pip install -e .

echo ""
echo "Done. Activate with:  source .venv/bin/activate"
echo "Then run:             python -m app --host localhost --port 9999"
