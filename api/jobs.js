const { isSignedIn } = require('../lib/auth');
const { db } = require('../lib/db');
const { send } = require('../lib/http');

const STATUSES = ['new', 'applied', 'interviewing', 'skipped', 'not_fit'];

module.exports = async (req, res) => {
  if (!isSignedIn(req)) return send(res, 401, { error: 'Sign in first.' });
  if (req.method !== 'GET') return send(res, 405, { error: 'Use GET.' });
  const url = new URL(req.url, 'http://local');
  const status = url.searchParams.get('status') || 'new';
  if (!STATUSES.includes(status)) return send(res, 400, { error: 'Unknown status.' });
  try {
    const pool = db();
    const order = status === 'new' ? 'fresh_at DESC' : 'COALESCE(status_at, fresh_at) DESC';
    const jobs = await pool.query(
      `SELECT id, source, company, title, location, url, alt_urls, posted_at, date_basis, first_seen_at, closed_at,
              fresh_at, score, tier, reason, possible_repost, status, status_at, applied_at
       FROM jobs WHERE status = $1 AND ($1 <> 'new' OR closed_at IS NULL)
       ORDER BY ${order} LIMIT 1000`,
      [status],
    );
    const counts = await pool.query(
      `SELECT status, count(*)::int AS count FROM jobs WHERE status <> 'new' OR closed_at IS NULL GROUP BY status`,
    );
    const lastRun = await pool.query(
      `SELECT finished_at, stats FROM runs WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1`,
    );
    const failing = await pool.query(
      `SELECT ats, slug, company, last_error FROM boards WHERE active AND last_error IS NOT NULL ORDER BY ats, slug`,
    );
    const manual = await pool.query(
      `SELECT started_at, finished_at, stats FROM runs WHERE trigger = 'manual' ORDER BY started_at DESC LIMIT 1`,
    );
    const searched = await pool.query(
      `SELECT split_part(key, ':', 1) AS source, max(last_success_at) AS at FROM search_state GROUP BY 1`,
    );
    const watching = await pool.query(`SELECT count(*)::int AS count FROM boards WHERE active AND last_error IS NULL`);
    return send(res, 200, {
      jobs: jobs.rows,
      counts: Object.fromEntries(counts.rows.map((row) => [row.status, row.count])),
      health: {
        lastRunAt: lastRun.rows[0]?.finished_at || null,
        sourceErrors: lastRun.rows[0]?.stats?.errors || [],
        warnings: lastRun.rows[0]?.stats?.warnings || [],
        lastManual: manual.rows[0] || null,
        searched: Object.fromEntries(searched.rows.map((row) => [row.source, row.at])),
        failing: failing.rows,
        watching: watching.rows[0].count,
      },
    });
  } catch (error) {
    console.error(error.message);
    return send(res, 500, { error: 'Could not load jobs from the database.' });
  }
};
