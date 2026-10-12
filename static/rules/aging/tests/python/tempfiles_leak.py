import tempfile


def convert(data):
    # ruleid: aging-py-tempfile-without-cleanup
    fd, path = tempfile.mkstemp(suffix=".gif")
    # ruleid: aging-py-tempfile-without-cleanup
    out = tempfile.NamedTemporaryFile(delete=False)
    # ok: aging-py-tempfile-without-cleanup
    with tempfile.TemporaryDirectory() as d:
        pass
    return path, out
