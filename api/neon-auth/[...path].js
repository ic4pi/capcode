// Vercel serverless proxy to Neon Auth. Runs entirely on Vercel's own infra
// (never touches Render) so the session cookie is set on THIS domain
// (first-party from the browser's view) instead of Neon's own cross-site
// auth domain — that cross-site cookie is exactly what Safari (and other
// strict-cookie browsers) blocks, which is why sign-in has been failing.
//
// NOTE: `config.api.bodyParser` is a Next.js convention. This project has
// "framework": null in vercel.json, so Vercel's plain Node runtime may or
// may not honor it -- don't assume either way. Handle both cases: if
// Vercel already parsed the body onto req.body, use that; otherwise read
// the raw stream ourselves.
module.exports.config = { api: { bodyParser: false } };

const NEON_AUTH_URL = process.env.NEON_AUTH_URL;

async function readBody(req) {
  if (req.body !== undefined && req.body !== null) {
    if (Buffer.isBuffer(req.body)) return req.body;
    if (typeof req.body === 'string') return Buffer.from(req.body);
    // Vercel auto-parsed JSON/form data into an object for us -- put it
    // back into the exact wire format Neon Auth expects.
    return Buffer.from(JSON.stringify(req.body));
  }
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks);
}

module.exports = async (req, res) => {
  try {
    if (!NEON_AUTH_URL) {
      res.status(500).json({ error: 'NEON_AUTH_URL is not set on Vercel' });
      return;
    }

    const segs = req.query.path;
    const path = Array.isArray(segs) ? segs.join('/') : segs || '';
    const qIndex = req.url.indexOf('?');
    const qs = qIndex >= 0 ? req.url.slice(qIndex) : '';
    const target = `${NEON_AUTH_URL.replace(/\/$/, '')}/${path}${qs}`;

    const headers = {};
    for (const [k, v] of Object.entries(req.headers)) {
      const lk = k.toLowerCase();
      if (['host', 'content-length', 'connection', 'x-forwarded-host',
           'x-forwarded-proto', 'x-forwarded-for', 'x-forwarded-port'].includes(lk)) continue;
      headers[k] = v;
    }

    const body = ['GET', 'HEAD'].includes(req.method) ? undefined : await readBody(req);

    let upstream;
    try {
      upstream = await fetch(target, { method: req.method, headers, body, redirect: 'manual' });
    } catch (exc) {
      res.status(502).json({ error: `neon auth upstream unreachable: ${exc}` });
      return;
    }

    const cookies = typeof upstream.headers.getSetCookie === 'function'
      ? upstream.headers.getSetCookie()
      : (upstream.headers.get('set-cookie') ? [upstream.headers.get('set-cookie')] : []);
    if (cookies.length) res.setHeader('set-cookie', cookies);

    upstream.headers.forEach((value, key) => {
      const lk = key.toLowerCase();
      if (['content-length', 'content-encoding', 'transfer-encoding', 'connection', 'set-cookie'].includes(lk)) return;
      res.setHeader(key, value);
    });

    const buf = Buffer.from(await upstream.arrayBuffer());
    res.status(upstream.status).send(buf);
  } catch (exc) {
    // Whatever the real bug turns out to be, surface it in the response
    // instead of letting Vercel return its generic, undiagnosable crash
    // page -- this line is the difference between guessing and knowing.
    res.status(500).json({ error: `neon-auth proxy crashed: ${exc && exc.stack ? exc.stack : String(exc)}` });
  }
};
