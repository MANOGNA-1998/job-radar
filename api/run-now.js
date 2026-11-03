const { isSignedIn } = require('../lib/auth');
const { send, isJsonPost } = require('../lib/http');

// Starts the same GitHub workflow the schedule uses. Paid searches protect themselves with a cooldown and daily cap.
module.exports = async (req, res) => {
  if (!isSignedIn(req)) return send(res, 401, { error: 'Sign in first.' });
  if (!isJsonPost(req)) return send(res, 405, { error: 'Use POST with JSON.' });
  const token = process.env.GITHUB_DISPATCH_TOKEN;
  const repo = process.env.GITHUB_REPO;
  if (!token || !repo) return send(res, 503, { error: 'Check now is not set up yet. Add GITHUB_DISPATCH_TOKEN and GITHUB_REPO in Vercel.' });
  const host = process.env.JR_GITHUB_API || 'https://api.github.com';
  const requestedAt = new Date().toISOString();
  try {
    const response = await fetch(`${host}/repos/${repo}/actions/workflows/collect.yml/dispatches`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'job-radar',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: process.env.GITHUB_BRANCH || 'main' }),
    });
    if (response.status !== 204) {
      console.error('GitHub refused the run', response.status);
      return send(res, 502, { error: `GitHub refused the check (status ${response.status}). Check the token and repository name in Vercel.` });
    }
    return send(res, 202, { requestedAt });
  } catch (error) {
    console.error(error.message);
    return send(res, 502, { error: 'Could not reach GitHub to start the check.' });
  }
};
