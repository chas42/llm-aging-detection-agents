const fs = require('fs');
const os = require('os');

function save(db, k) {
  // retention exists in this file
  // ok: aging-js-insert-without-retention
  db.run("INSERT INTO kv (k) VALUES (?)", [k]);
  db.run("DELETE FROM kv WHERE ts < ?", [Date.now() - 3600e3]);
}

function convert(buf) {
  // ok: aging-js-tempfile-without-cleanup
  const dir = fs.mkdtempSync(os.tmpdir() + '/x-');
  try { return buf; } finally { fs.rmSync(dir, { recursive: true, force: true }); }
}
