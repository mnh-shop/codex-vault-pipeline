#!/usr/bin/env bash
# Codex Vault Pipeline — environment bootstrap script.
#
# This script sets up the project-local Python environment using uv.
# Run it once after cloning the pipeline repo.
#
# Usage:
#   ./scripts/bootstrap_env.sh
#
# Requirements:
#   - uv must be installed (brew install uv)
#   - Internet access for downloading dependencies
set -euo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PIPELINE_DIR"

echo "=== Codex Vault Pipeline — Environment Bootstrap ==="
echo "Pipeline dir: $PIPELINE_DIR"
echo ""

# Check for uv
if ! command -v uv &>/dev/null; then
    echo "ERROR: uv not found."
    echo "Install with: brew install uv"
    exit 1
fi

echo "uv version: $(uv --version)"
echo ""

# Create .python-version if missing
if [ ! -f .python-version ]; then
    echo "Creating .python-version (3.11)..."
    echo "3.11" > .python-version
fi

# Create venv
if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    uv venv
else
    echo "Virtual environment already exists."
fi

# Sync dependencies
echo ""
echo "Syncing dependencies..."
uv sync --all-extras --dev

echo ""
echo "=== Bootstrap complete ==="
echo ""
echo "To activate the environment:"
echo "  source .venv/bin/activate"
echo ""
echo "Or use uv run:"
echo "  uv run codex-vault --help"
echo "  uv run codex-vault env doctor"
echo "  uv run codex-vault paths doctor --vault-root /path/to/vault"
echo "  uv run pytest"
