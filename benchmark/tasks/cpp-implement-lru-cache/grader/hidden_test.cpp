#include <cassert>
#include <cstddef>
#include <iostream>
#include <optional>
#include <string>
#include <utility>

#include "lru_cache.h"

// Hidden edge-case tests injected by the grader.

static void test_empty_and_miss() {
  cache::LruCache<std::string, int> lru(3);
  assert(lru.size() == 0);
  assert(!lru.get("a").has_value());
  assert(!lru.get("b").has_value());
  // Misses must not change size or recency.
  assert(lru.size() == 0);
}

static void test_zero_capacity_stores_nothing() {
  cache::LruCache<std::string, int> lru(0);
  lru.put("a", 1);
  lru.put("b", 2);
  assert(lru.size() == 0);
  assert(!lru.get("a").has_value());
  assert(!lru.get("b").has_value());
}

static void test_update_existing_key() {
  cache::LruCache<std::string, int> lru(2);
  lru.put("a", 1);
  lru.put("b", 2);
  lru.put("a", 10);
  assert(lru.size() == 2);
  assert(lru.get("a") == std::optional<int>(10));
  assert(lru.get("b") == std::optional<int>(2));
}

static void test_update_refreshes_recency() {
  cache::LruCache<std::string, int> lru(2);
  lru.put("a", 1);
  lru.put("b", 2);
  // Updating "a" makes it most recently used, so "b" is evicted next.
  lru.put("a", 10);
  lru.put("c", 3);
  assert(lru.size() == 2);
  assert(lru.get("a") == std::optional<int>(10));
  assert(!lru.get("b").has_value());
  assert(lru.get("c") == std::optional<int>(3));
}

static void test_get_refreshes_recency() {
  cache::LruCache<std::string, int> lru(3);
  lru.put("a", 1);
  lru.put("b", 2);
  lru.put("c", 3);
  // Touch "a" and "b"; "c" becomes least recently used.
  assert(lru.get("a") == std::optional<int>(1));
  assert(lru.get("b") == std::optional<int>(2));
  lru.put("d", 4);
  assert(lru.size() == 3);
  assert(lru.get("a") == std::optional<int>(1));
  assert(lru.get("b") == std::optional<int>(2));
  assert(lru.get("d") == std::optional<int>(4));
  assert(!lru.get("c").has_value());
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

  // "b" is now least recently used (after the get above refreshed "c").
  lru.put("d", 4);
  assert(lru.size() == 2);
  assert(!lru.get("b").has_value());
  assert(lru.get("c") == std::optional<int>(3));
  assert(lru.get("d") == std::optional<int>(4));
}

static void test_capacity_one() {
  cache::LruCache<std::string, int> lru(1);
  lru.put("a", 1);
  assert(lru.size() == 1);
  assert(lru.get("a") == std::optional<int>(1));
  lru.put("b", 2);
  assert(lru.size() == 1);
  assert(!lru.get("a").has_value());
  assert(lru.get("b") == std::optional<int>(2));
}

static void test_non_string_key_and_value() {
  cache::LruCache<int, std::string> lru(2);
  lru.put(1, "one");
  lru.put(2, "two");
  assert(lru.size() == 2);
  assert(lru.get(1) == std::optional<std::string>("one"));
  assert(lru.get(2) == std::optional<std::string>("two"));

  // Refresh key 1, then insert key 3 to evict key 2.
  assert(lru.get(1) == std::optional<std::string>("one"));
  lru.put(3, "three");
  assert(lru.size() == 2);
  assert(lru.get(1) == std::optional<std::string>("one"));
  assert(!lru.get(2).has_value());
  assert(lru.get(3) == std::optional<std::string>("three"));
}

static void test_pair_value_type() {
  cache::LruCache<std::string, std::pair<int, int>> lru(2);
  lru.put("a", std::make_pair(1, 2));
  lru.put("b", std::make_pair(3, 4));
  assert((lru.get("a") ==
          std::optional<std::pair<int, int>>(std::make_pair(1, 2))));
  assert((lru.get("b") ==
          std::optional<std::pair<int, int>>(std::make_pair(3, 4))));
}

int main() {
  test_empty_and_miss();
  test_zero_capacity_stores_nothing();
  test_update_existing_key();
  test_update_refreshes_recency();
  test_get_refreshes_recency();
  test_eviction_order();
  test_capacity_one();
  test_non_string_key_and_value();
  test_pair_value_type();
  std::cout << "Hidden tests passed" << std::endl;
  return 0;
}
