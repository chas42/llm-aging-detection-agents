function start() {
  // ok: aging-js-interval-without-clear
  const t = setInterval(() => {}, 1000);
  return () => clearInterval(t);
}
