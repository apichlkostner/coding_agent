#ifndef CALCULATOR_H
#define CALCULATOR_H

#include <optional>

namespace calc {

// Returns lhs + rhs when the sum is representable as an int, otherwise
// std::nullopt. The addition must not be evaluated when it would overflow.
std::optional<int> checked_add(int lhs, int rhs);

}  // namespace calc

#endif  // CALCULATOR_H
