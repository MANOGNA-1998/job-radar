const { passwordMatches, setSession } = require('../lib/auth');
const { send, readJson, isJsonPost } = require('../lib/http');

module.exports = async (req, res) => {
  if (!isJsonPost(req)) return send(res, 405, { error: 'Use POST with JSON.' });
  let body;
  try {
    body = await readJson(req);
  } catch {
    return send(res, 400, { error: 'Invalid request.' });
  }
  if (!passwordMatches(body.password)) {
    await new Promise((resolve) => setTimeout(resolve, 800));
    return send(res, 401, { error: 'That password is not right.' });
  }
  setSession(req, res);
  return send(res, 200, { ok: true });
};
