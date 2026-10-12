import os
import shutil
import tempfile


def convert(data):
    # ok: aging-py-tempfile-without-cleanup
    d = tempfile.mkdtemp()
    try:
        return d
    finally:
        shutil.rmtree(d, ignore_errors=True)
