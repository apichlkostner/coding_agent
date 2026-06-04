#include "hello.h"

int main() {
    Greeter g("world");
    std::string msg = g.greet();
    return msg.empty() ? 1 : 0;
}
