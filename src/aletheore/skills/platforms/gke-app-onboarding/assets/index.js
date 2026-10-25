const http = require('http');

const port = process.env.PORT || 8080;

// Flipped once the server is listening; gate readiness on any startup work.
let ready = false;

const server = http.createServer((req, res) => {
  if (req.url === '/healthz') {
    // Liveness: the process is up and able to serve requests
    res.statusCode = 200;
    res.setHeader('Content-Type', 'text/plain');
    res.end('OK');
  } else if (req.url === '/readyz') {
    // Readiness: only accept traffic once startup is complete
    res.statusCode = ready ? 200 : 503;
    res.setHeader('Content-Type', 'text/plain');
    res.end(ready ? 'READY' : 'NOT READY');
  } else {
    res.statusCode = 200;
    res.setHeader('Content-Type', 'text/plain');
    res.end('Hello, World!\n');
  }
});

server.listen(port, () => {
  ready = true;
  console.log(`Server running on port ${port}`);
});
