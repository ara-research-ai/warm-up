import cloudflareAccess from '@cloudflare/pages-plugin-cloudflare-access';

const deny = status => new Response('Sign in with an Ara Google account.', {
  status, headers: { 'Cache-Control': 'private, no-store', 'X-Robots-Tag': 'noindex, nofollow' },
});

export async function onRequest(context) {
  const domain = context.env.CF_ACCESS_DOMAIN;
  const audiences = (context.env.CF_ACCESS_AUD || '').split(',').filter(Boolean);
  if (!/^https:\/\/[a-z0-9-]+\.cloudflareaccess\.com$/.test(domain || '') || !audiences.length) return deny(503);
  const token = context.request.headers.get('Cf-Access-Jwt-Assertion');
  if (!token) return deny(403);
  try {
    // Only select among configured audiences. The plugin verifies the signature.
    const encoded = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const unverified = JSON.parse(atob(encoded));
    const aud = audiences.find(value => Array.isArray(unverified.aud) && unverified.aud.includes(value));
    if (!aud) return deny(403);
    const response = await cloudflareAccess({ domain, aud })({ ...context, next: async () => {
      const jwt = context.data.cloudflareAccess.JWT;
      const payload = jwt.payload;
      if (payload.iss !== domain || !Array.isArray(payload.aud) || !payload.aud.includes(aud) ||
          !Number.isFinite(payload.exp) || payload.exp <= Date.now() / 1000 ||
          !/^[^@]+@ara-research\.ai$/i.test(payload.email || '')) return deny(403);
      const identity = await jwt.getIdentity();
      if (!identity || !['google', 'gsuite'].includes(identity.idp?.type) ||
          identity.email?.toLowerCase() !== payload.email.toLowerCase()) return deny(403);
      const asset = await context.next();
      const protectedResponse = new Response(asset.body, asset);
      protectedResponse.headers.set('Cache-Control', 'private, no-store');
      protectedResponse.headers.set('X-Robots-Tag', 'noindex, nofollow');
      return protectedResponse;
    } });
    // Cloudflare Access handles the browser login. The origin denies bad tokens.
    return response.status >= 300 && response.status < 400 ? deny(403) : response;
  } catch {
    return deny(403);
  }
}
