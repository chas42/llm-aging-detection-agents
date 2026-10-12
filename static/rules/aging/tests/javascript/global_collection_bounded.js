const cache = new Map();

function put(k, v) {
  // eviction exists in this file
  // ok: aging-js-global-collection-growth
  cache.set(k, v);
  if (cache.size > 1000) cache.delete(cache.keys().next().value);
}
