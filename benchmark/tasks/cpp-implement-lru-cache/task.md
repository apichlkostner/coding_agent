# Task: Implement a bounded LRU cache

`LruCache<Key, Value>` in `include/lru_cache.h` is a header-only template for a
bounded cache with least-recently-used eviction. The header currently declares
the full public interface but the implementation is a stub: it stores entries
without tracking recency and never evicts, so it does not honor the capacity
contract. Implement the documented behavior.

## Requirements

- `explicit LruCache(std::size_t capacity)` constructs a cache that holds at
  most `capacity` entries. A capacity of `0` is valid and stores no entries.
- `std::optional<Value> get(const Key& key)` returns the stored value when
  `key` is present, and `std::nullopt` when it is absent. A successful `get`
  makes `key` the most recently used entry. A miss must not change recency.
- `void put(const Key& key, const Value& value)` inserts or updates `key` with
  `value` and makes `key` the most recently used entry. Updating an existing
  key must not increase `size()`. Inserting a new key when the cache is already
  at capacity evicts the least recently used entry first.
- `std::size_t size() const` returns the number of stored entries.
- When the capacity is `0`, `put` stores nothing and `get` always returns
  `std::nullopt`.
- `Key` must be usable as a key in `std::unordered_map` (hashable and
  equality-comparable). `Value` must be copyable. The implementation must work
  for non-string keys and values.
- Expected average complexity for `get` and `put` is $O(1)$.
- Do not change the public interface in `include/lru_cache.h`.
- Do not modify `check.sh` or the files under `tests/`.
- Your change must pass the quality gate: run `./check.sh` and make sure it
  exits 0. This includes the build (`-Wall -Wextra -Werror -Wpedantic`),
  `clang-format`, `clang-tidy`, and the test binary.

## Acceptance

The grader runs `check.sh` plus additional hidden tests that exercise empty and
zero-capacity caches, hits and misses, updates, recency refresh on `get`,
least-recently-used eviction, and at least one non-string key/value
instantiation. Passing the visible tests alone is not sufficient.
