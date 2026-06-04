#pragma once

#include <string>

class Greeter {
public:
    Greeter(std::string name);
    std::string greet() const;
    const std::string& name() const;

private:
    std::string name_;
};
