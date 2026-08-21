// Vercel serverless proxy to Neon Auth. Runs entirely on Vercel's own infra
// (never touches Render) so the session cookie is set on THIS domain
// (first-party from the browser's view) instead of Neon's own cross-site
// auth domain — that cross-site cookie is exactly what Safari (and other
// strict-cookie browsers) blocks, which is why sign-in has been failing.
module.exports.config = { api: { bodyParser: false } };

const NEON_AUTH_URL = process.env.NEON_AUTH_URL;

module.exports = async (req, res) => {
  if (!NEON_AUTH_URL) {
    res.status(500).json({ error: "NEON_AUTH_URL is not set on Vercel" });
    return;
  }

  const segs = req.query.path;
  const path = Array.isArray(segs) ? segs.join("/") : segs || "";
  const qIndex = req.url.indexOf("?");
  const qs = qIndex >= 0 ? req.url.slice(qIndex) : "";
  const target = `${NEON_AUTH_URL.replace(/\/$/, "")}/${path}${qs}`;

  const headers = {};
  for (const [k, v] of Object.entries(req.headers)) {
    const lk = k.toLowerCase();
    if (["host", "content-length", "connection", "x-forwarded-host",
         "x-forwarded-proto", "x-forwarded-for", "x-forwarded-port"].includes(lk)) continue;
    headers[k] = v;
  }

  let body;
  if (!["GET", "HEAD"].includes(req.method)) {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    body = Buffer.concat(chunks);
  }

  let upstream;
  try {
    upstream = await fetch(target, { method: req.method, headers, body, redirect: "manual" });
  } catch (exc) {
    res.status(502).json({ error: `neon auth upstream unreachable: ${exc}` });
    return;
  }

  const cookies = typeof upstream.headers.getSetCookie === "function"
    ? upstream.headers.getSetCookie()
    : (upstream.headers.get("set-cookie") ? [upstream.headers.get("set-cookie")] : []);
  if (cookies.length) res.setHeader("set-cookie", cookies);

  upstream.headers.forEach((value, key) => {
    const lk = key.toLowerCase();
    if (["content-length", "content-encoding", "transfer-encoding", "connection", "set-cookie"].includes(lk)) return;
    res.setHeader(key, value);
  });

  const buf = Buffer.from(await upstream.arrayBuffer());
  res.status(upstream.status).send(buf);
};
