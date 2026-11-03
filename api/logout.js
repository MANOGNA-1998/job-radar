const { clearSession } = require('../lib/auth');
const { send, isJsonPost } = require('../lib/http');

module.exports = async (req, res) => {
  if (!isJsonPost(req)) return send(res, 405, { error: 'Use POST with JSON.' });
  clearSession(req, res);
  return send(res, 200, { ok: true });
};
