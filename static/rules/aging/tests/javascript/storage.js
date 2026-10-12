const sqlite3 = require('sqlite3');
const os = require('os');
const fs = require('fs');
const path = require('path');
const db = new sqlite3.Database('uptime.db');

function heartbeat(serviceId, token) {
  // ruleid: aging-js-insert-without-retention
  db.run("INSERT OR REPLACE INTO services (serviceId, token, lastNotification) VALUES (?, ?, ?)", [serviceId, token, Date.now()]);
}

function list(token, cb) {
  // ruleid: aging-js-select-without-limit
  db.all("SELECT serviceId, lastNotification FROM services WHERE token = ?", [token], cb);
  // ok: aging-js-select-without-limit
  db.all("SELECT * FROM services WHERE token = ? LIMIT 50", [token], cb);
  // ok: aging-js-select-without-limit
  db.all("SELECT COUNT(*) AS n FROM services", [], cb);
}

function convert(buf) {
  // ruleid: aging-js-tempfile-without-cleanup
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gif-'));
  fs.writeFileSync(path.join(dir, 'out.gif'), buf);
  return dir;
}
