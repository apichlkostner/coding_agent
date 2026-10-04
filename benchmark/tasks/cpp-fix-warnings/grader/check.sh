#!/usr/bin/env bash
# Authoritative grader for the C++ task.
# Runs the public quality gate, then injects and runs hidden edge-case tests.
set -euo pipefail

CXX="${CXX:-g++}"
CXXFLAGS="-std=c++17 -Wall -Wextra -Werror -Wpedantic -Iinclude"

echo "==> Public quality gate"
./check.sh

echo "==> Hidden tests"
$CXX $CXXFLAGS src/calculator.cpp grader/hidden_test.cpp -o /tmp/hidden_test
/tmp/hidden_test

echo "GRADER: PASS"
