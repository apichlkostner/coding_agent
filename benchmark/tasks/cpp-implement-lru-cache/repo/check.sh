#!/usr/bin/env bash
# Quality gate for the C++ task. Must exit 0 for the change to be accepted.
set -euo pipefail

CXX="${CXX:-g++}"
CXXFLAGS="-std=c++17 -Wall -Wextra -Werror -Wpedantic -Iinclude"

echo "==> Build"
$CXX $CXXFLAGS -c src/main.cpp -o /tmp/main.o
$CXX $CXXFLAGS tests/test_lru_cache.cpp -o /tmp/test_lru_cache

echo "==> Format check"
clang-format --dry-run --Werror src/*.cpp include/*.h tests/*.cpp

echo "==> Static analysis"
clang-tidy src/main.cpp -- $CXXFLAGS

echo "==> Tests"
/tmp/test_lru_cache

echo "All checks passed"
