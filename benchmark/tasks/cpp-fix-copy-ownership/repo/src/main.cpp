#include <cstddef>
#include <iostream>

#include "sample_buffer.h"

int main() {
  sample::SampleBuffer buffer(4);
  for (std::size_t i = 0; i < buffer.size(); ++i) {
    buffer.set(i, static_cast<int>(i) * 10);
  }

  sample::SampleBuffer copy = buffer;
  copy.set(0, 99);

  std::cout << "original[0] = " << buffer.get(0) << "\n";
  std::cout << "copy[0] = " << copy.get(0) << "\n";
  return 0;
}
