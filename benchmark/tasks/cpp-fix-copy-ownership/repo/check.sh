#!/usr/bin/env bash
# Quality gate for the C++ task. Must exit 0 for the change to be accepted.
set -euo pipefail

CXX="${CXX:-g++}"
CXXFLAGS="-std=c++17 -Wall -Wextra -Werror -Wpedantic -Iinclude"

echo "==> Build"
$CXX $CXXFLAGS -c src/sample_buffer.cpp -o /tmp/sample_buffer.o
$CXX $CXXFLAGS -c src/main.cpp -o /tmp/main.o
$CXX $CXXFLAGS src/sample_buffer.cpp tests/test_sample_buffer.cpp -o /tmp/test_sample_buffer

echo "==> Format check"
clang-format --dry-run --Werror src/*.cpp include/*.h tests/*.cpp

echo "==> Static analysis"
clang-tidy src/sample_buffer.cpp src/main.cpp -- $CXXFLAGS

echo "==> Tests"
/tmp/test_sample_buffer

echo "All checks passed"
