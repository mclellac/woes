#!/bin/bash
# Script to run unit tests

echo "Running unit tests..."
python -m unittest discover -s src/tests
