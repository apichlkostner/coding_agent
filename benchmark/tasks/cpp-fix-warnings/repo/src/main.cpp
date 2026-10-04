#include <iostream>
#include <string>
#include <vector>

#include "calculator.h"

int main() {
  std::string line;
  std::cout << "Enter numbers separated by spaces: ";
  std::getline(std::cin, line);

  std::vector<int> values;
  if (!calc::parse_numbers(line, values)) {
    std::cerr << "Invalid input\n";
    return 1;
  }

  std::cout << "sum = " << calc::sum(values) << "\n";
  std::cout << "mean = " << calc::mean(values) << "\n";
  return 0;
}
