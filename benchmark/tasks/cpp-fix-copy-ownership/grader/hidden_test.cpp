#include <cassert>
#include <cstddef>
#include <iostream>

#include "sample_buffer.h"

// Hidden edge-case tests injected by the grader.

static void test_deep_copy_independence() {
  sample::SampleBuffer original(3);
  original.set(0, 1);
  original.set(1, 2);
  original.set(2, 3);

  sample::SampleBuffer copy(original);
  assert(copy.size() == 3);
  assert(copy.get(0) == 1);
  assert(copy.get(1) == 2);
  assert(copy.get(2) == 3);

  // Mutating the copy must not affect the original.
  copy.set(0, 100);
  copy.set(2, 300);
  assert(original.get(0) == 1);
  assert(original.get(2) == 3);

  // Mutating the original must not affect the copy.
  original.set(1, 200);
  assert(copy.get(1) == 2);
}

static void test_copy_assignment_over_existing_storage() {
  sample::SampleBuffer source(2);
  source.set(0, 11);
  source.set(1, 22);

  sample::SampleBuffer target(5);
  for (std::size_t i = 0; i < target.size(); ++i) {
    target.set(i, -1);
  }

  target = source;
  assert(target.size() == 2);
  assert(target.get(0) == 11);
  assert(target.get(1) == 22);

  // The target must own independent storage after assignment.
  target.set(0, 111);
  assert(target.get(0) == 111);
  assert(source.get(0) == 11);
}

static void test_self_assignment() {
  sample::SampleBuffer buffer(3);
  buffer.set(0, 5);
  buffer.set(1, 6);
  buffer.set(2, 7);

  sample::SampleBuffer& alias = buffer;
  buffer = alias;

  assert(buffer.size() == 3);
  assert(buffer.get(0) == 5);
  assert(buffer.get(1) == 6);
  assert(buffer.get(2) == 7);
}

static void test_zero_length_behavior() {
  sample::SampleBuffer empty(0);
  assert(empty.size() == 0);
  assert(empty.get(0) == 0);
  empty.set(0, 42);
  assert(empty.get(0) == 0);

  // Copying an empty buffer yields an independent empty buffer.
  sample::SampleBuffer copy(empty);
  assert(copy.size() == 0);
  assert(copy.get(0) == 0);

  // Assigning an empty buffer over a non-empty one resizes to zero.
  sample::SampleBuffer other(4);
  other.set(0, 9);
  other = empty;
  assert(other.size() == 0);
  assert(other.get(0) == 0);
}

static void test_out_of_range_access() {
  sample::SampleBuffer buffer(2);
  buffer.set(0, 1);
  buffer.set(1, 2);

  // Out-of-range set is a no-op; out-of-range get returns 0.
  buffer.set(2, 99);
  buffer.set(100, 99);
  assert(buffer.get(2) == 0);
  assert(buffer.get(100) == 0);
  assert(buffer.get(0) == 1);
  assert(buffer.get(1) == 2);
}

int main() {
  test_deep_copy_independence();
  test_copy_assignment_over_existing_storage();
  test_self_assignment();
  test_zero_length_behavior();
  test_out_of_range_access();
  std::cout << "Hidden tests passed" << std::endl;
  return 0;
}
