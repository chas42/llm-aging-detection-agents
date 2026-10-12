import sqlite3


def save(service_id, token, now):
    conn = sqlite3.connect("db.sqlite3")
    # ruleid: aging-py-insert-without-retention
    conn.execute(
        """
        INSERT INTO heartbeats(service_id, token, last_notification)
        VALUES (?, ?, ?)
        ON CONFLICT(service_id, token) DO UPDATE SET last_notification = excluded.last_notification
        """,
        (service_id, token, now),
    )
    # ok: aging-py-insert-without-retention
    conn.execute("UPDATE heartbeats SET token = ? WHERE token = ?", (token, token))
    conn.commit()


def store(model_cls, data):
    # ruleid: aging-py-insert-without-retention
    model_cls.objects.create(**data)
