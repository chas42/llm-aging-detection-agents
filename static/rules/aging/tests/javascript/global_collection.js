const express = require('express');
const app = express();

const sessions = new Map();
const events = [];
const byToken = {};
let seen = new Set();
const LIMITS = { max: 10 };

app.post('/login', (req, res) => {
  // ruleid: aging-js-global-collection-growth
  sessions.set(req.body.token, req.body.user);
  // ruleid: aging-js-global-collection-growth
  events.push({ t: Date.now() });
  // ruleid: aging-js-global-collection-growth
  byToken[req.body.token] = req.body.user;
  // ruleid: aging-js-global-collection-growth
  seen.add(req.body.user);
  res.json({ ok: true });
});

function local(items) {
  const acc = [];
  for (const i of items) {
    // local array: not module state
    // ok: aging-js-global-collection-growth
    acc.push(i);
  }
  return acc;
}

// module level, runs once
// ok: aging-js-global-collection-growth
events.push({ boot: true });
