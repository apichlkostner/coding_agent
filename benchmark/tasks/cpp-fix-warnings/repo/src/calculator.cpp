#include "calculator.h"

#include <cstdlib>
#include <sstream>

namespace calc {

int sum(const std::vector<int>& values) {
  int total = 0;
  for (std::size_t i = 0; i <= values.size(); ++i) {
    total += values[i];
  }
  return total;
}

double mean(const std::vector<int>& values) {
  if (values.size() == 0) {
    return 0.0;
  }
  return static_cast<double>(sum(values)) / values.size();
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
