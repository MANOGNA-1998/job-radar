const crypto = require('crypto');

const COOKIE = 'jr_session';
const MAX_AGE_SECONDS = 60 * 60 * 24 * 30;

function secret() {
  const value = process.env.SESSION_SECRET;
  if (!value || value.length < 32) throw new Error('SESSION_SECRET must be at least 32 characters');
  return value;
}

function sign(expires) {
  return crypto.createHmac('sha256', secret()).update(String(expires)).digest('hex');
}

function safeEqual(a, b) {
  const left = crypto.createHash('sha256').update(String(a)).digest();
  const right = crypto.createHash('sha256').update(String(b)).digest();
  return crypto.timingSafeEqual(left, right);
}

function cookieAttributes(req, maxAge) {
  const local = /^(localhost|127\.0\.0\.1)(:|$)/.test(req.headers.host || '');
  return `Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAge}${local ? '' : '; Secure'}`;
}

function setSession(req, res) {
  const expires = Math.floor(Date.now() / 1000) + MAX_AGE_SECONDS;
  res.setHeader('Set-Cookie', `${COOKIE}=${expires}.${sign(expires)}; ${cookieAttributes(req, MAX_AGE_SECONDS)}`);
}

function clearSession(req, res) {
  res.setHeader('Set-Cookie', `${COOKIE}=; ${cookieAttributes(req, 0)}`);
}

function isSignedIn(req) {
  const match = (req.headers.cookie || '').match(new RegExp(`(?:^|;\\s*)${COOKIE}=(\\d+)\\.([a-f0-9]+)`));
  if (!match) return false;
  if (Number(match[1]) < Date.now() / 1000) return false;
  return safeEqual(match[2], sign(match[1]));
}

function passwordMatches(candidate) {
  const expected = process.env.DASHBOARD_PASSWORD;
  if (!expected) return false;
  return safeEqual(candidate || '', expected);
}

module.exports = { isSignedIn, setSession, clearSession, passwordMatches };
