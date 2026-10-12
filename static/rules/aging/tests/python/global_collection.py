import collections
from collections import defaultdict, deque

SESSIONS = {}
EVENTS = []
SEEN = set()
BY_USER = collections.defaultdict(list)
RECENT = collections.deque()
LAST_N = collections.deque(maxlen=100)
CONFIG = {"a": 1}

# module-level mutation (import time, runs once): not per request
# ok: aging-py-global-collection-growth
CONFIG["b"] = 2


def login(user, token):
    # ruleid: aging-py-global-collection-growth
    SESSIONS[token] = user
    # ruleid: aging-py-global-collection-growth
    EVENTS.append(("login", user))
    # ruleid: aging-py-global-collection-growth
    SEEN.add(user)
    # ruleid: aging-py-global-collection-growth
    BY_USER[user].append(token)
    # ruleid: aging-py-global-collection-growth
    RECENT.append(token)
    # bounded deque
    # ok: aging-py-global-collection-growth
    LAST_N.append(token)


async def hit(key):
    # ruleid: aging-py-global-collection-growth
    SESSIONS.setdefault(key, 0)


def local_only(items):
    acc = []
    for i in items:
        # local variable, not module state
        # ok: aging-py-global-collection-growth
        acc.append(i)
    return acc
