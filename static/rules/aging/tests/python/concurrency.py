import asyncio
import concurrent.futures
import threading

BACKGROUND = set()


def handle(job):
    # ruleid: aging-py-executor-per-call
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    # ok: aging-py-executor-per-call
    with concurrent.futures.ThreadPoolExecutor() as pool:
        pool.submit(job)
    # ruleid: aging-py-executor-per-call
    threading.Thread(target=job).start()
    return ex


# module level (once): not per call
# ok: aging-py-executor-per-call
POOL = concurrent.futures.ThreadPoolExecutor()


async def on_request(coro):
    # ruleid: aging-py-fire-and-forget-task
    asyncio.create_task(coro())
    # ok: aging-py-fire-and-forget-task
    t = asyncio.create_task(coro())
    # the set keeps every task forever (no add_done_callback(BACKGROUND.discard)): C1 growth
    # ruleid: aging-py-global-collection-growth
    # ok: aging-py-fire-and-forget-task
    BACKGROUND.add(asyncio.create_task(coro()))
    # ok: aging-py-fire-and-forget-task
    await asyncio.create_task(coro())
    return t


TRACKED = set()


async def tracked(coro):
    t = asyncio.create_task(coro())
    # the done-callback removes it -> bounded by in-flight tasks
    # ok: aging-py-global-collection-growth
    TRACKED.add(t)
    t.add_done_callback(TRACKED.discard)
