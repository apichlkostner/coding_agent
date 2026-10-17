#ifndef LRU_CACHE_H
#define LRU_CACHE_H

#include <cstddef>
#include <optional>
#include <unordered_map>

namespace cache {

// A bounded cache with least-recently-used eviction.
//
// The cache holds at most `capacity` entries. A successful get() and every
// put() make the accessed key the most recently used entry. Inserting a new
// key when the cache is full evicts the least recently used entry. A capacity
// of 0 stores no entries.
//
// Key must be hashable and equality-comparable (usable as a std::unordered_map
// key). Value must be copyable. Expected average complexity for get() and
// put() is O(1).
template <typename Key, typename Value>
class LruCache {
 public:
  // Constructs a cache that holds at most `capacity` entries.
  explicit LruCache(std::size_t capacity) : capacity_(capacity) {}

  // Returns the value for `key`, or std::nullopt when absent. A hit makes
  // `key` the most recently used entry; a miss does not change recency.
  [[nodiscard]] std::optional<Value> get(const Key& key) {
    const auto it = entries_.find(key);
    if (it == entries_.end()) {
      return std::nullopt;
    }
    return it->second;
  }

  // Inserts or updates `key` with `value` and makes it the most recently used
  // entry. Updating an existing key does not increase size(). Inserting a new
  // key at capacity evicts the least recently used entry first.
  void put(const Key& key, const Value& value) { entries_[key] = value; }

  // Returns the number of stored entries.
  [[nodiscard]] std::size_t size() const { return entries_.size(); }

 private:
  std::size_t capacity_ = 0;
  std::unordered_map<Key, Value> entries_;
};

}  // namespace cache

#endif  // LRU_CACHE_H
