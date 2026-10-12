def save(conn, k, v):
    # a retention path exists in this file -> not flagged
    # ok: aging-py-insert-without-retention
    conn.execute("INSERT INTO kv(k, v) VALUES (?, ?)", (k, v))


def cleanup(conn, cutoff):
    conn.execute("DELETE FROM kv WHERE ts < ?", (cutoff,))
