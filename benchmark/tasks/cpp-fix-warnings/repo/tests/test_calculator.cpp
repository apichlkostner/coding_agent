#include <cassert>
#include <iostream>
#include <vector>

#include "calculator.h"

static void test_sum() {
  assert(calc::sum({}) == 0);
  assert(calc::sum({1, 2, 3}) == 6);
  assert(calc::sum({-5, 5}) == 0);
}

static void test_mean() {
  assert(calc::mean({}) == 0.0);
  assert(calc::mean({2, 4}) == 3.0);
}

static void test_parse() {
  std::vector<int> out;
  assert(calc::parse_numbers("1 2 3", out));
  assert(out.size() == 3);
  assert(out[0] == 1 && out[2] == 3);

  out.clear();
  assert(!calc::parse_numbers("1 x 3", out));
}

int main() {
  test_sum();
  test_mean();
  test_parse();
  std::cout << "All tests passed" << std::endl;
  return 0;
}
