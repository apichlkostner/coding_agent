#include "calculator.h"

#include <cstdlib>
#include <sstream>

namespace calc {

int sum(const std::vector<int>& values) {
  int total = 0;
  for (const int value : values) {
    total += value;
  }
  return total;
}

double mean(const std::vector<int>& values) {
  if (values.empty()) {
    return 0.0;
  }
  return static_cast<double>(sum(values)) / static_cast<double>(values.size());
}

bool parse_numbers(const std::string& input, std::vector<int>& out) {
  std::istringstream stream(input);
  std::string token;
  while (stream >> token) {
    char* end = nullptr;
    long value = std::strtol(token.c_str(), &end, 10);
    if (end == token.c_str() || *end != '\0') {
      return false;
    }
    out.push_back(static_cast<int>(value));
  }
  return true;
}

}  // namespace calc
