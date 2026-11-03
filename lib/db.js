const { Pool } = require('pg');

let pool;

function db() {
  if (!pool) {
    const local = /@(localhost|127\.0\.0\.1)[:/]/.test(process.env.DATABASE_URL || '');
    pool = new Pool({
      connectionString: process.env.DATABASE_URL,
      max: 1,
      idleTimeoutMillis: 10000,
      // Supabase's pooler presents a certificate that isn't in Node's default store. The link is still encrypted.
      ssl: local ? false : { rejectUnauthorized: false },
    });
  }
  return pool;
}

module.exports = { db };
