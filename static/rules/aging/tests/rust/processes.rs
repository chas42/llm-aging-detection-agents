use std::process::Command;
use std::time::Duration;

fn ps() -> String {
    // ruleid: aging-rs-command-without-timeout
    let out = Command::new("ps").arg("aux").output().unwrap();
    String::from_utf8_lossy(&out.stdout).to_string()
}

async fn aps() -> Vec<u8> {
    // ok: aging-rs-command-without-timeout
    let out = tokio::time::timeout(Duration::from_secs(5), tokio::process::Command::new("ps").arg("aux").output()).await;
    out.unwrap().unwrap().stdout
}

fn per_call() {
    // ruleid: aging-rs-thread-per-call
    std::thread::spawn(|| println!("work"));
    // ok: aging-rs-thread-per-call
    let h = std::thread::spawn(|| 1);
    h.join().unwrap();
}

fn convert(bytes: &[u8]) -> std::path::PathBuf {
    // ruleid: aging-rs-tempfile-without-cleanup
    let p = std::env::temp_dir().join("out.gif");
    std::fs::write(&p, bytes).unwrap();
    p
}

fn save(conn: &rusqlite::Connection, k: &str) {
    // ruleid: aging-rs-insert-without-retention
    conn.execute("INSERT INTO kv (k) VALUES (?1)", [k]).unwrap();
}
