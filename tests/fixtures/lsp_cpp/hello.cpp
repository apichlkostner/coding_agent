#include "hello.h"

Greeter::Greeter(std::string name) : name_(std::move(name)) {}

std::string Greeter::greet() const {
    return "Hello, " + name_ + "!";
}

const std::string& Greeter::name() const {
    return name_;
}
