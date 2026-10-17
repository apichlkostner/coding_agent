#include "calculator.h"

#include <limits>

namespace calc {

std::optional<int> checked_add(int lhs, int rhs) {
  const int sum = lhs + rhs;
  if (sum > std::numeric_limits<int>::max() ||
      sum < std::numeric_limits<int>::min()) {
    return std::nullopt;
  }
  return sum;
}

}  // namespace calc
