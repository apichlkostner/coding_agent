#include <cassert>
#include <iostream>
#include <limits>
#include <optional>

#include "calculator.h"

static void test_checked_add_normal() {
  assert(calc::checked_add(1, 2) == std::optional<int>(3));
  assert(calc::checked_add(-5, 5) == std::optional<int>(0));
  assert(calc::checked_add(0, 0) == std::optional<int>(0));
}

static void test_checked_add_overflow() {
  const int max = std::numeric_limits<int>::max();
  const int min = std::numeric_limits<int>::min();
  assert(!calc::checked_add(max, 1).has_value());
  assert(!calc::checked_add(min, -1).has_value());
}

int main() {
  test_checked_add_normal();
  test_checked_add_overflow();
  std::cout << "All tests passed" << std::endl;
  return 0;
}
