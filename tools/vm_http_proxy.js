const http = require("http");
const net = require("net");

const bindAddress = "192.168.46.208";
const allowedClient = "192.168.46.236";
const port = 3128;

function permitted(socket) {
  const address = socket.remoteAddress?.replace(/^::ffff:/, "");
  return address === allowedClient;
}

const server = http.createServer((request, response) => {
  if (!permitted(request.socket)) {
    response.writeHead(403);
    response.end("Forbidden\n");
    return;
  }

  let target;
  try {
    target = new URL(request.url);
  } catch {
    response.writeHead(400);
    response.end("Absolute URL required\n");
    return;
  }

  const headers = { ...request.headers, host: target.host };
  delete headers["proxy-connection"];

  const upstream = http.request(
    {
      hostname: target.hostname,
      port: target.port || 80,
      method: request.method,
      path: `${target.pathname}${target.search}`,
      headers,
    },
    (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    },
  );

  upstream.on("error", (error) => {
    if (!response.headersSent) {
      response.writeHead(502);
    }
    response.end(`Proxy error: ${error.message}\n`);
  });

  request.pipe(upstream);
});

server.on("connect", (request, client, head) => {
  if (!permitted(client)) {
    client.end("HTTP/1.1 403 Forbidden\r\n\r\n");
    return;
  }

  const separator = request.url.lastIndexOf(":");
  const host = separator >= 0 ? request.url.slice(0, separator) : request.url;
  const targetPort = separator >= 0 ? Number(request.url.slice(separator + 1)) : 443;
  const upstream = net.connect(targetPort, host);

  upstream.once("connect", () => {
    client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
    if (head.length) upstream.write(head);
    upstream.pipe(client);
    client.pipe(upstream);
  });

  const close = () => {
    client.destroy();
    upstream.destroy();
  };
  upstream.on("error", close);
  client.on("error", close);
});

server.listen(port, bindAddress, () => {
  console.log(`VM proxy listening on ${bindAddress}:${port} for ${allowedClient}`);
});
