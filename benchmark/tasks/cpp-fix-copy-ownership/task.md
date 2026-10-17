# Task: Fix copy and assignment ownership in `SampleBuffer`

`SampleBuffer` in `src/sample_buffer.cpp` owns a dynamically allocated array of
`int` values. Its copy constructor and copy assignment currently copy the
internal pointer instead of the owned storage, so two `SampleBuffer` objects
share the same array. Mutating one object changes the other, and destroying
either object leaves the other with a dangling pointer (double free / use after
free). Find and fix the root cause so that `SampleBuffer` has correct value
semantics.

## Requirements

- `SampleBuffer(std::size_t size)` allocates storage for `size` integers,
  value-initialized to `0`. A size of `0` is valid and allocates no storage.
- `void set(std::size_t index, int value)` stores `value` at `index`.
- `int get(std::size_t index) const` returns the value stored at `index`.
- `std::size_t size() const` returns the number of elements.
- The destructor releases the owned storage exactly once.
- The copy constructor produces a deep copy: the new object owns its own
  storage with the same size and the same element values.
- Copy assignment replaces the destination's contents with a deep copy of the
  source, is safe when the source and destination are the same object
  (self-assignment), and does not leak the destination's previous storage.
- Copies are independent: mutating one object must not affect any other.
- Out-of-range access (index `>= size()`) must be handled consistently: `set`
  and `get` must not read or write outside the owned storage. The chosen
  behavior must be documented in the header and applied uniformly.
- Do not change the public interface in `include/sample_buffer.h`.
- Do not modify `check.sh` or the files under `tests/`.
- Your change must pass the quality gate: run `./check.sh` and make sure it
  exits 0. This includes the build (`-Wall -Wextra -Werror -Wpedantic`),
  `clang-format`, `clang-tidy`, and the test binary.

## Acceptance

The grader runs `check.sh` plus additional hidden tests that exercise deep-copy
independence, copy assignment over existing storage, self-assignment, and
zero-length buffers. Passing the visible tests alone is not sufficient.
