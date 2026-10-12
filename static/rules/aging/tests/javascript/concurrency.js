const { exec, spawn } = require('child_process');
const child_process = require('child_process');
const EventEmitter = require('events');
const express = require('express');
const app = express();
const bus = new EventEmitter();

app.get('/processes', (req, res) => {
  // ruleid: aging-js-child-process-without-timeout
  exec('ps aux', (err, out) => res.send(out));
  // ok: aging-js-child-process-without-timeout
  exec('ps aux', { timeout: 5000 }, (err, out) => res.send(out));
  // ruleid: aging-js-child-process-without-timeout
  const p = child_process.spawn('ps', ['aux']);
  // per-request stream: GC'd with the request
  // ok: aging-js-listener-in-handler
  p.stdout.on('data', (d) => res.write(d));
  // ruleid: aging-js-listener-in-handler
  process.on('exit', () => console.log(req.url));
  // ruleid: aging-js-listener-in-handler
  bus.on('event', () => res.end());
  // ok: aging-js-listener-in-handler
  req.on('close', () => {});
  // a regex .exec is not a child process
  // ok: aging-js-child-process-without-timeout
  const m = /a(b)/.exec(req.url);
});

app.post('/check', async (req, res) => {
  // ruleid: aging-js-fetch-without-timeout
  const r = await fetch(req.body.url);
  // ok: aging-js-fetch-without-timeout
  const r2 = await fetch(req.body.url, { signal: AbortSignal.timeout(5000) });
  res.json({ status: r.status, s2: r2.status });
});

// ruleid: aging-js-interval-without-clear
setInterval(() => console.log('tick'), 1000);
