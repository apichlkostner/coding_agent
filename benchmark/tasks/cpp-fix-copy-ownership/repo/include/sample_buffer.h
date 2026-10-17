#ifndef SAMPLE_BUFFER_H
#define SAMPLE_BUFFER_H

#include <cstddef>

namespace sample {

// A fixed-size buffer that owns a dynamically allocated array of int values.
//
// Value semantics: copies own independent storage. Mutating one buffer never
// affects another.
//
// Out-of-range access: an index >= size() is a no-op for set() and returns 0
// for get(). No access outside the owned storage is performed.
class SampleBuffer {
 public:
  // Allocates storage for `size` integers, each value-initialized to 0.
  // A size of 0 is valid and allocates no storage.
  explicit SampleBuffer(std::size_t size);

  // Deep copy: the new buffer owns its own storage with the same size and
  // element values.
  SampleBuffer(const SampleBuffer& other);

  // Deep copy assignment. Safe for self-assignment and does not leak the
  // destination's previous storage.
  SampleBuffer& operator=(const SampleBuffer& other);

  // Releases the owned storage exactly once.
  ~SampleBuffer();

  // Stores `value` at `index`. Out-of-range indices are ignored.
  void set(std::size_t index, int value);

  // Returns the value at `index`, or 0 when `index` is out of range.
  [[nodiscard]] int get(std::size_t index) const;

  // Returns the number of elements.
  [[nodiscard]] std::size_t size() const;

 private:
  std::size_t size_;
  int* data_ = nullptr;
};

}  // namespace sample

#endif  // SAMPLE_BUFFER_H
