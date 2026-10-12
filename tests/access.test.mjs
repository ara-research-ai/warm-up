import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { generateKeyPairSync, sign } from 'node:crypto';

test('every static path requires a valid Access token from an Ara Google identity', async () => {
  const { onRequest } = await import('../functions/_middleware.js');
  const { publicKey, privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
  const key = { ...publicKey.export({ format: 'jwk' }), kid: 'test', alg: 'RS256' };
  const domain = 'https://example.cloudflareaccess.com';
  const audience = 'test-audience';
  const now = Math.floor(Date.now() / 1000);
  let identity = { email: 'processor@ara-research.ai', idp: { type: 'google' } };
  const server = createServer((request, response) => {
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify(request.url.endsWith('/certs') ? { keys: [key] } : identity));
  }).listen(0, '127.0.0.1');
  await new Promise(resolve => server.on('listening', resolve));
  const realFetch = globalThis.fetch;
  globalThis.fetch = (url, options) => {
    assert.equal(new URL(url).origin, domain);
    return realFetch(`http://127.0.0.1:${server.address().port}${new URL(url).pathname}`, options);
  };
  const jwt = (changes = {}, signingKey = privateKey) => {
    const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url');
    const body = encode({ alg: 'RS256', kid: 'test' }) + '.' + encode({
      iss: domain, aud: [audience], exp: now + 300, iat: now, email: identity.email, ...changes,
    });
    return body + '.' + sign('RSA-SHA256', Buffer.from(body), signingKey).toString('base64url');
  };
  let reads = 0;
  let assetResponse = () => new Response('private snapshot');
  const run = (path, token, env = { CF_ACCESS_DOMAIN: domain, CF_ACCESS_AUD: audience }) => onRequest({
    request: new Request('https://ara-warm-up.pages.dev' + path, { headers: token ? { 'Cf-Access-Jwt-Assertion': token } : {} }),
    env, data: {}, functionPath: '/', waitUntil() {}, passThroughOnException() {},
    next: () => { reads++; return assetResponse(); },
  });
  try {
    for (const path of ['/', '/archive.json', '/snapshots/2026-10-12/example.html']) {
      assert.equal((await run(path)).status, 403);
      assert.equal((await run(path, 'forged')).status, 403);
    }
    assert.equal((await run('/', jwt(), {})).status, 503);
    for (const changes of [{ aud: ['other-app'] }, { iss: 'https://other.cloudflareaccess.com' }, { exp: now - 1 }, { exp: null }, { aud: null }, { iss: null }, { email: 'person@outside.example' }]) {
      assert.equal((await run('/', jwt(changes))).status, 403);
    }
    const otherKey = generateKeyPairSync('rsa', { modulusLength: 2048 }).privateKey;
    assert.equal((await run('/', jwt({}, otherKey))).status, 403);
    identity.idp.type = 'onetimepin';
    assert.equal((await run('/', jwt())).status, 403);
    assert.equal(reads, 0);
    identity.idp.type = 'google';
    const response = await run('/archive.json', jwt());
    assert.equal(response.status, 200);
    assert.equal(await response.text(), 'private snapshot');
    assert.equal(response.headers.get('Cache-Control'), 'private, no-store');
    assert.equal(reads, 1);
    // Pages redirects .html requests to their extensionless URL after authentication.
    assetResponse = () => new Response(null, { status: 302, headers: { Location: '/snapshots/example' } });
    const redirect = await run('/snapshots/example.html', jwt());
    assert.equal(redirect.status, 302);
    assert.equal(redirect.headers.get('Location'), '/snapshots/example');
    assert.equal(redirect.headers.get('Cache-Control'), 'private, no-store');
  } finally {
    globalThis.fetch = realFetch;
    await new Promise(resolve => server.close(resolve));
  }
});
