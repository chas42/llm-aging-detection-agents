import sqlite3


def list_services(token):
    conn = sqlite3.connect("db.sqlite3")
    # ruleid: aging-py-select-without-limit
    cur = conn.execute("SELECT service_id, last FROM heartbeats WHERE token = ? ORDER BY service_id", (token,))
    rows = cur.fetchall()
    # ok: aging-py-select-without-limit
    page = conn.execute("SELECT * FROM heartbeats WHERE token = ? LIMIT 100", (token,)).fetchall()
    # ok: aging-py-select-without-limit
    one = conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()
    return rows, page, one


async def alist(db, token):
    # ruleid: aging-py-select-without-limit
    async with db.execute("SELECT * FROM services WHERE token = ?", (token,)) as cur:
        return await cur.fetchall()


def django_views(Service, token):
    # ruleid: aging-py-select-without-limit
    everything = list(Service.objects.filter(token=token).order_by("name"))
    # ok: aging-py-select-without-limit
    latest = Service.objects.filter(token=token).first()
    # ok: aging-py-select-without-limit
    page = Service.objects.filter(token=token)[:50]
    # ok: aging-py-select-without-limit
    n = Service.objects.all().count()
    return everything, latest, page, n
