#include "sample_buffer.h"

#include <cstddef>

namespace sample {

SampleBuffer::SampleBuffer(std::size_t size) : size_(size) {
  if (size_ > 0) {
    data_ = new int[size_]{};
  }
}

SampleBuffer::SampleBuffer(const SampleBuffer& other)
    : size_(other.size_), data_(other.data_) {}

SampleBuffer& SampleBuffer::operator=(const SampleBuffer& other) {
  size_ = other.size_;
  data_ = other.data_;
  return *this;
}

SampleBuffer::~SampleBuffer() { delete[] data_; }

void SampleBuffer::set(std::size_t index, int value) {
  if (index < size_) {
    data_[index] = value;
  }
}

int SampleBuffer::get(std::size_t index) const {
  if (index < size_) {
    return data_[index];
  }
  return 0;
}

std::size_t SampleBuffer::size() const { return size_; }

}  // namespace sample
