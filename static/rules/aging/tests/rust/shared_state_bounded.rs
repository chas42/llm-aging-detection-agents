use std::collections::HashMap;
use std::sync::Mutex;

fn put(m: &Mutex<HashMap<String, u64>>, k: String) {
    let mut g = m.lock().unwrap();
    // eviction exists in this file
    // ok: aging-rs-shared-map-growth
    g.insert(k, 1);
    if g.len() > 1000 {
        g.retain(|_, v| *v > 0);
    }
}
