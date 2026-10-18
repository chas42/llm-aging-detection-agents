# Functional tests adapted from the paper

`reference/tests/` keeps the paper's tests unchanged. Tests that cannot run as written are adapted here,
keeping their checks:

| test | why adapted | what changed |
|---|---|---|
| `test_convert_image.py` | the original reads `frame.png` from the author's home directory, posts to a hardcoded `localhost:3000`, and is not named `func_test*` | the second frame is generated in memory (solid 100x100 PNG); the port comes from the harness; same requests, GIF and frame-count checks; the delay check follows the API spec (delay in ms, GIF precision 10 ms) instead of the original `duration // 10 == delay`, which reads the delay as 1/100 s, contradicts the spec, and fails for a spec-compliant app |
