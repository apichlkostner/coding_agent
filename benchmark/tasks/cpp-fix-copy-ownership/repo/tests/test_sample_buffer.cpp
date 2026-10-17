#include <cassert>
#include <cstddef>
#include <iostream>

#include "sample_buffer.h"

static void test_construction_and_access() {
  sample::SampleBuffer buffer(3);
  assert(buffer.size() == 3);
  assert(buffer.get(0) == 0);
  assert(buffer.get(1) == 0);
  assert(buffer.get(2) == 0);

  buffer.set(0, 10);
  buffer.set(1, 20);
  buffer.set(2, 30);
  assert(buffer.get(0) == 10);
  assert(buffer.get(1) == 20);
  assert(buffer.get(2) == 30);
}

static void test_copy_constructor_is_independent() {
  sample::SampleBuffer original(2);
  original.set(0, 1);
  original.set(1, 2);

  sample::SampleBuffer copy(original);
  assert(copy.size() == 2);
  assert(copy.get(0) == 1);
  assert(copy.get(1) == 2);

  copy.set(0, 100);
  assert(copy.get(0) == 100);
  assert(original.get(0) == 1);
}

static void test_copy_assignment_is_independent() {
  sample::SampleBuffer source(2);
  source.set(0, 7);
  source.set(1, 8);

  sample::SampleBuffer target(3);
  target.set(0, -1);
  target = source;

  assert(target.size() == 2);
  assert(target.get(0) == 7);
  assert(target.get(1) == 8);

  target.set(1, 80);
  assert(target.get(1) == 80);
  assert(source.get(1) == 8);
}

static void test_zero_size() {
  sample::SampleBuffer empty(0);
  assert(empty.size() == 0);
  assert(empty.get(0) == 0);
  empty.set(0, 5);
  assert(empty.get(0) == 0);
}

int main() {
  test_construction_and_access();
  test_copy_constructor_is_independent();
  test_copy_assignment_is_independent();
  test_zero_size();
  std::cout << "All tests passed" << std::endl;
  return 0;
}
