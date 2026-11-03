CREATE TABLE IF NOT EXISTS boards (
  id bigserial PRIMARY KEY,
  ats text NOT NULL,
  slug text NOT NULL,
  company text,
  config jsonb NOT NULL DEFAULT '{}',
  origin text NOT NULL DEFAULT 'seed',
  active boolean NOT NULL DEFAULT true,
  first_crawl_done boolean NOT NULL DEFAULT false,
  last_ok_at timestamptz,
  last_error text,
  last_error_at timestamptz,
  job_count int NOT NULL DEFAULT 0,
  UNIQUE (ats, slug)
);
CREATE TABLE IF NOT EXISTS jobs (
  id bigserial PRIMARY KEY,
  source text NOT NULL,
  ext_id text NOT NULL,
  board_id bigint REFERENCES boards(id) ON DELETE SET NULL,
  dedup_key text NOT NULL,
  company text NOT NULL,
  title text NOT NULL,
  location text,
  url text NOT NULL,
  alt_urls jsonb NOT NULL DEFAULT '[]',
  description text,
  posted_at timestamptz,
  date_basis text NOT NULL,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  closed_at timestamptz,
  fresh_at timestamptz NOT NULL,
  score int NOT NULL,
  tier text NOT NULL,
  reason text NOT NULL,
  possible_repost boolean NOT NULL DEFAULT false,
  status text NOT NULL DEFAULT 'new',
  status_at timestamptz,
  applied_at timestamptz,
  UNIQUE (source, ext_id)
);
CREATE INDEX IF NOT EXISTS jobs_status_fresh ON jobs (status, fresh_at DESC);
CREATE INDEX IF NOT EXISTS jobs_dedup ON jobs (dedup_key);
CREATE TABLE IF NOT EXISTS runs (
  id bigserial PRIMARY KEY,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  stats jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS company_probes (
  company_key text PRIMARY KEY,
  probed_at timestamptz NOT NULL DEFAULT now(),
  found text
);
CREATE TABLE IF NOT EXISTS apify_usage (
  day date NOT NULL,
  source text NOT NULL,
  results int NOT NULL DEFAULT 0,
  last_run_at timestamptz,
  PRIMARY KEY (day, source)
);
CREATE TABLE IF NOT EXISTS search_state (
  key text PRIMARY KEY,
  last_attempt_at timestamptz,
  last_success_at timestamptz
);
CREATE TABLE IF NOT EXISTS jsearch_usage (
  month date PRIMARY KEY,
  requests int NOT NULL DEFAULT 0
);
ALTER TABLE runs ADD COLUMN IF NOT EXISTS trigger text NOT NULL DEFAULT 'schedule';
-- Supabase exposes tables through a public API unless row level security is on.
-- The collector and dashboard connect as the database owner, which bypasses this.
ALTER TABLE boards ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_probes ENABLE ROW LEVEL SECURITY;
ALTER TABLE apify_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE search_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE jsearch_usage ENABLE ROW LEVEL SECURITY;
