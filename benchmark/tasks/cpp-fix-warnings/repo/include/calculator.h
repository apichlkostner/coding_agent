#ifndef CALCULATOR_H
#define CALCULATOR_H

#include <string>
#include <vector>

namespace calc {

// Sums all values in the vector. Returns 0 for an empty vector.
int sum(const std::vector<int>& values);

// Returns the arithmetic mean of the values.
// Returns 0.0 for an empty vector.
double mean(const std::vector<int>& values);

// Parses a whitespace-separated list of integers.
// Returns false if any token is not a valid integer.
bool parse_numbers(const std::string& input, std::vector<int>& out);

}  // namespace calc

#endif  // CALCULATOR_H
