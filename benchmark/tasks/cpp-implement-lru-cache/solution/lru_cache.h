#ifndef LRU_CACHE_H
#define LRU_CACHE_H

#include <cstddef>
#include <list>
#include <optional>
#include <unordered_map>
#include <utility>

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
    const auto it = index_.find(key);
    if (it == index_.end()) {
      return std::nullopt;
    }
    Touch(it->second);
    return it->second->second;
  }

  // Inserts or updates `key` with `value` and makes it the most recently used
  // entry. Updating an existing key does not increase size(). Inserting a new
  // key at capacity evicts the least recently used entry first.
  void put(const Key& key, const Value& value) {
    if (capacity_ == 0) {
      return;
    }

    const auto it = index_.find(key);
    if (it != index_.end()) {
      it->second->second = value;
      Touch(it->second);
      return;
    }

    if (order_.size() >= capacity_) {
      const Key& victim = order_.back().first;
      index_.erase(victim);
      order_.pop_back();
    }

    order_.emplace_front(key, value);
    index_[key] = order_.begin();
  }

  // Returns the number of stored entries.
  [[nodiscard]] std::size_t size() const { return order_.size(); }

 private:
  using Entry = std::pair<Key, Value>;
  using Iterator = typename std::list<Entry>::iterator;

  // Moves the entry referenced by `it` to the front (most recently used).
  void Touch(Iterator it) { order_.splice(order_.begin(), order_, it); }

  std::size_t capacity_ = 0;
  std::list<Entry> order_;
  std::unordered_map<Key, Iterator> index_;
};

}  // namespace cache

#endif  // LRU_CACHE_H
