#include <cassert>
#include <iostream>
#include <vector>

#include "calculator.h"

// Hidden edge-case tests injected by the grader.
static void test_sum_edge_cases() {
  assert(calc::sum({}) == 0);
  assert(calc::sum({42}) == 42);
  assert(calc::sum({1, 2, 3, 4, 5}) == 15);
  assert(calc::sum({-1, -2, -3}) == -6);
  assert(calc::sum({1000000, 2000000}) == 3000000);
}

static void test_mean_edge_cases() {
  assert(calc::mean({}) == 0.0);
  assert(calc::mean({7}) == 7.0);
  assert(calc::mean({1, 2}) == 1.5);
}

static void test_parse_edge_cases() {
  std::vector<int> out;
  assert(calc::parse_numbers("", out));
  assert(out.empty());

  out.clear();
  assert(calc::parse_numbers("  10   20  ", out));
  assert(out.size() == 2 && out[0] == 10 && out[1] == 20);

  out.clear();
  assert(!calc::parse_numbers("12abc", out));
}

int main() {
  test_sum_edge_cases();
  test_mean_edge_cases();
  test_parse_edge_cases();
  std::cout << "Hidden tests passed" << std::endl;
  return 0;
}
