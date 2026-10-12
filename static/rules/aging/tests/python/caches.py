import functools
from functools import lru_cache, cache


# ruleid: aging-py-unbounded-cache
@functools.lru_cache(maxsize=None)
def resolve(name):
    return name.upper()


# ruleid: aging-py-unbounded-cache
@cache
def lookup(key):
    return key


# ok: aging-py-unbounded-cache
@lru_cache(maxsize=256)
def bounded(key):
    return key
