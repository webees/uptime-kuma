// Minimal backend for the C2/C1 proxy cases: it answers with the headers it
// saw, so the assertions read Caddy's forwarding instead of the app's own.
// It lives in the repository because the harness binds it into the container;
// an untracked /tmp copy silently produced empty headers on CI.
require("http")
  .createServer((req, res) => {
    const body = JSON.stringify(req.headers);
    res.writeHead(200, { "content-length": Buffer.byteLength(body) });
    res.end(body);
  })
  .listen(3001);
