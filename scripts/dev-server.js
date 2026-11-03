// Runs the dashboard locally the way Vercel serves it. Needs DATABASE_URL, DASHBOARD_PASSWORD, SESSION_SECRET.
const fs = require('fs');
const http = require('http');
const path = require('path');

const routes = {
  '/api/login': require('../api/login'),
  '/api/logout': require('../api/logout'),
  '/api/jobs': require('../api/jobs'),
  '/api/job-status': require('../api/job-status'),
  '/api/run-now': require('../api/run-now'),
};

http.createServer(async (req, res) => {
  const { pathname } = new URL(req.url, 'http://local');
  if (routes[pathname]) return routes[pathname](req, res);
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.end(fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html')));
}).listen(Number(process.env.PORT || 3000), () => console.log(`Dashboard on http://localhost:${process.env.PORT || 3000}`));
