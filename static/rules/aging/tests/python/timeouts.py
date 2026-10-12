import asyncio
import os
import subprocess
import urllib.request

import httpx
import requests


def check(url):
    # ruleid: aging-py-http-without-timeout
    requests.get(url)
    # ok: aging-py-http-without-timeout
    requests.get(url, timeout=5)
    # ruleid: aging-py-http-without-timeout
    urllib.request.urlopen(url)
    # ok: aging-py-http-without-timeout
    urllib.request.urlopen(url, timeout=3)
    # ruleid: aging-py-http-without-timeout
    httpx.get(url, timeout=None)
    # ok: aging-py-http-without-timeout
    httpx.get(url)


def processes(cmd):
    # ruleid: aging-py-subprocess-without-timeout
    subprocess.run(cmd, capture_output=True)
    # ok: aging-py-subprocess-without-timeout
    subprocess.run(cmd, capture_output=True, timeout=10)
    # ruleid: aging-py-subprocess-without-timeout
    subprocess.check_output(["ps", "aux"])
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    # ruleid: aging-py-subprocess-without-timeout
    out, _ = p.communicate()
    # ok: aging-py-subprocess-without-timeout
    out, _ = p.communicate(timeout=5)
    # ruleid: aging-py-subprocess-without-timeout
    os_output = os.popen("ps").read()
    return out, os_output


async def aprocess(cmd):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE)
    # ruleid: aging-py-subprocess-without-timeout
    out, _ = await proc.communicate()
    # ok: aging-py-subprocess-without-timeout
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    return out
