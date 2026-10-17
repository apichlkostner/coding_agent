#include <cstddef>
#include <iostream>
#include <string>

#include "lru_cache.h"

int main() {
  cache::LruCache<std::string, int> lru(2);
  lru.put("a", 1);
  lru.put("b", 2);
  lru.put("c", 3);

  std::cout << "size = " << lru.size() << "\n";
  const std::optional<int> a = lru.get("a");
  std::cout << "a present = " << (a.has_value() ? "yes" : "no") << "\n";
  return 0;
}
