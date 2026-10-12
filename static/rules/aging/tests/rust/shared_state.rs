use actix_web::{web, HttpResponse};
use std::collections::HashMap;
use std::sync::Mutex;

struct AppState {
    db: Mutex<HashMap<String, HashMap<String, u64>>>,
    log: Mutex<Vec<String>>,
}

async fn heartbeat(state: web::Data<AppState>, body: web::Json<(String, String)>) -> HttpResponse {
    let mut db = state.db.lock().unwrap();
    // ruleid: aging-rs-shared-map-growth
    db.entry(body.1.clone()).or_insert_with(HashMap::new).insert(body.0.clone(), 1);
    // ruleid: aging-rs-shared-map-growth
    state.log.lock().unwrap().push(body.0.clone());
    HttpResponse::Ok().finish()
}

fn local() -> Vec<u32> {
    let mut v = Vec::new();
    // local vector, not shared state
    // ok: aging-rs-shared-map-growth
    v.push(1);
    v
}

fn leak(s: String) -> &'static str {
    // ruleid: aging-rs-explicit-leak
    Box::leak(s.into_boxed_str())
}
