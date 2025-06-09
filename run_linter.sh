#!/bin/bash

echo "Running Ruff linter..."
ruff check src/*.py --fix
