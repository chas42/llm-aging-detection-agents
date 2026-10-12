fn convert(bytes: &[u8]) {
    // ok: aging-rs-tempfile-without-cleanup
    let p = std::env::temp_dir().join("out.gif");
    std::fs::write(&p, bytes).unwrap();
    std::fs::remove_file(&p).unwrap();
}

fn save(conn: &rusqlite::Connection, k: &str) {
    // ok: aging-rs-insert-without-retention
    conn.execute("INSERT INTO kv (k) VALUES (?1)", [k]).unwrap();
    conn.execute("DELETE FROM kv WHERE ts < ?1", [0]).unwrap();
}
