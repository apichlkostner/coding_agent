#include "calculator.h"

#include <limits>

namespace calc {

std::optional<int> checked_add(int lhs, int rhs) {
  const int max = std::numeric_limits<int>::max();
  const int min = std::numeric_limits<int>::min();
  if (rhs > 0 && lhs > max - rhs) {
    return std::nullopt;
  }
  if (rhs < 0 && lhs < min - rhs) {
    return std::nullopt;
  }
  return lhs + rhs;
}

}  // namespace calc
