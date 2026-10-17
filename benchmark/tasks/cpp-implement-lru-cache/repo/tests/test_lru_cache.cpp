#include <cassert>
#include <cstddef>
#include <iostream>
#include <optional>
#include <string>

#include "lru_cache.h"

static void test_empty_cache() {
  cache::LruCache<std::string, int> lru(2);
  assert(lru.size() == 0);
  assert(!lru.get("missing").has_value());
}

static void test_zero_capacity() {
  cache::LruCache<std::string, int> lru(0);
  lru.put("a", 1);
  assert(lru.size() == 0);
  assert(!lru.get("a").has_value());
}

static void test_put_and_get() {
  cache::LruCache<std::string, int> lru(2);
  lru.put("a", 1);
  lru.put("b", 2);
  assert(lru.size() == 2);
  assert(lru.get("a") == std::optional<int>(1));
  assert(lru.get("b") == std::optional<int>(2));
  assert(!lru.get("c").has_value());
}

static void test_update_does_not_grow() {
  cache::LruCache<std::string, int> lru(2);
  lru.put("a", 1);
  lru.put("a", 10);
  assert(lru.size() == 1);
  assert(lru.get("a") == std::optional<int>(10));
}

static void test_get_refreshes_recency() {
  cache::LruCache<std::string, int> lru(2);
  lru.put("a", 1);
  lru.put("b", 2);
  // Touch "a" so "b" becomes the least recently used entry.
  assert(lru.get("a") == std::optional<int>(1));
  lru.put("c", 3);
  assert(lru.size() == 2);
  assert(lru.get("a") == std::optional<int>(1));
  assert(!lru.get("b").has_value());
  assert(lru.get("c") == std::optional<int>(3));
}

static void test_eviction_order() {
  cache::LruCache<std::string, int> lru(2);
  lru.put("a", 1);
  lru.put("b", 2);
  lru.put("c", 3);
  assert(lru.size() == 2);
  assert(!lru.get("a").has_value());
  assert(lru.get("b") == std::optional<int>(2));
  assert(lru.get("c") == std::optional<int>(3));
}

int main() {
  test_empty_cache();
  test_zero_capacity();
  test_put_and_get();
  test_update_does_not_grow();
  test_get_refreshes_recency();
  test_eviction_order();
  std::cout << "All tests passed" << std::endl;
  return 0;
}
