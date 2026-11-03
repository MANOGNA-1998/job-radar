const { isSignedIn } = require('../lib/auth');
const { db } = require('../lib/db');
const { send, readJson, isJsonPost } = require('../lib/http');

const STATUSES = ['new', 'applied', 'interviewing', 'skipped', 'not_fit'];

module.exports = async (req, res) => {
  if (!isSignedIn(req)) return send(res, 401, { error: 'Sign in first.' });
  if (!isJsonPost(req)) return send(res, 405, { error: 'Use POST with JSON.' });
  let body;
  try {
    body = await readJson(req);
  } catch {
    return send(res, 400, { error: 'Invalid request.' });
  }
  const id = Number(body.id);
  if (!Number.isInteger(id) || !STATUSES.includes(body.status)) return send(res, 400, { error: 'Invalid job or status.' });
  try {
    const result = await db().query(
      `UPDATE jobs SET status = $2, status_at = now(),
         applied_at = CASE WHEN $2 = 'applied' THEN COALESCE(applied_at, now())
                           WHEN $2 = 'interviewing' THEN applied_at ELSE NULL END
       WHERE id = $1 RETURNING id, status, applied_at`,
      [id, body.status],
    );
    if (!result.rowCount) return send(res, 404, { error: 'That job no longer exists.' });
    return send(res, 200, result.rows[0]);
  } catch (error) {
    console.error(error.message);
    return send(res, 500, { error: 'Could not save that change.' });
  }
};
