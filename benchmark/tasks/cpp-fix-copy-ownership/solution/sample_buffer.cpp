#include "sample_buffer.h"

#include <cstddef>
#include <utility>

namespace sample {

SampleBuffer::SampleBuffer(std::size_t size) : size_(size) {
  if (size_ > 0) {
    data_ = new int[size_]{};
  }
}

SampleBuffer::SampleBuffer(const SampleBuffer& other) : size_(other.size_) {
  if (size_ > 0) {
    data_ = new int[size_];
    for (std::size_t i = 0; i < size_; ++i) {
      data_[i] = other.data_[i];
    }
  }
}

SampleBuffer& SampleBuffer::operator=(const SampleBuffer& other) {
  if (this == &other) {
    return *this;
  }

  int* new_data = nullptr;
  if (other.size_ > 0) {
    new_data = new int[other.size_];
    for (std::size_t i = 0; i < other.size_; ++i) {
      new_data[i] = other.data_[i];
    }
  }

  delete[] data_;
  data_ = new_data;
  size_ = other.size_;
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
