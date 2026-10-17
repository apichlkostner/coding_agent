#!/usr/bin/env bash
# Quality gate for the C++ task. Must exit 0 for the change to be accepted.
set -euo pipefail

CXX="${CXX:-g++}"
CXXFLAGS="-std=c++17 -Wall -Wextra -Werror -Wpedantic -Iinclude"

echo "==> Build"
$CXX $CXXFLAGS -c src/calculator.cpp -o /tmp/calculator.o
$CXX $CXXFLAGS -c src/main.cpp -o /tmp/main.o
$CXX $CXXFLAGS src/calculator.cpp tests/test_calculator.cpp -o /tmp/test_calculator

echo "==> Format check"
clang-format --dry-run --Werror src/*.cpp include/*.h tests/*.cpp

echo "==> Static analysis"
clang-tidy src/calculator.cpp src/main.cpp -- $CXXFLAGS

echo "==> Tests"
/tmp/test_calculator

echo "All checks passed"
