#include <iostream>
#include <optional>

#include "calculator.h"

int main() {
  int lhs = 0;
  int rhs = 0;
  std::cout << "Enter two integers: ";
  if (!(std::cin >> lhs >> rhs)) {
    std::cerr << "Invalid input\n";
    return 1;
  }

  const std::optional<int> result = calc::checked_add(lhs, rhs);
  if (result.has_value()) {
    std::cout << "sum = " << *result << "\n";
  } else {
    std::cout << "overflow\n";
  }
  return 0;
}
