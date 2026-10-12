CACHE = {}


def put(k, v):
    # the file removes entries elsewhere -> not flagged (eviction exists)
    # ok: aging-py-global-collection-growth
    CACHE[k] = v


def evict(k):
    CACHE.pop(k, None)
