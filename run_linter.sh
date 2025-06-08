#!/bin/bash
# Script to run the primary linter (Ruff)

echo "Running Ruff linter..."
ruff check src/*.py --fix
