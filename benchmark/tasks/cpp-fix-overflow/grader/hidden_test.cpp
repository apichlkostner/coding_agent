#include <cassert>
#include <iostream>
#include <limits>
#include <optional>

#include "calculator.h"

// Hidden edge-case tests injected by the grader.
static void test_checked_add_boundaries() {
  const int max = std::numeric_limits<int>::max();
  const int min = std::numeric_limits<int>::min();

  assert(calc::checked_add(max, 0) == std::optional<int>(max));
  assert(calc::checked_add(0, max) == std::optional<int>(max));
  assert(calc::checked_add(min, 0) == std::optional<int>(min));
  assert(calc::checked_add(0, min) == std::optional<int>(min));

  assert(calc::checked_add(max, -1) == std::optional<int>(max - 1));
  assert(calc::checked_add(min, 1) == std::optional<int>(min + 1));
  assert(calc::checked_add(max, min) == std::optional<int>(-1));
}

static void test_checked_add_overflow() {
  const int max = std::numeric_limits<int>::max();
  const int min = std::numeric_limits<int>::min();

  assert(!calc::checked_add(max, 1).has_value());
  assert(!calc::checked_add(1, max).has_value());
  assert(!calc::checked_add(max, max).has_value());
  assert(!calc::checked_add(min, -1).has_value());
  assert(!calc::checked_add(-1, min).has_value());
  assert(!calc::checked_add(min, min).has_value());
}

static void test_checked_add_mixed_signs() {
  assert(calc::checked_add(1000000, 2000000) == std::optional<int>(3000000));
  assert(calc::checked_add(-1000000, -2000000) == std::optional<int>(-3000000));
  assert(calc::checked_add(7, -3) == std::optional<int>(4));
  assert(calc::checked_add(-7, 3) == std::optional<int>(-4));
}

int main() {
  test_checked_add_boundaries();
  test_checked_add_overflow();
  test_checked_add_mixed_signs();
  std::cout << "Hidden tests passed" << std::endl;
  return 0;
}
