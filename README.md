# Job Radar

Collects fresh Applied AI, Applied ML, and Software Engineering roles around the clock and shows them on a private dashboard, newest first, with a tracker for what you have applied to.

- **Collector:** `collector/`, run by GitHub Actions
- **Dashboard:** `public/` and `api/`, hosted on Vercel
- **Database:** Postgres on Supabase
- **What counts as a match:** `config/profile.yaml`
- **Which companies are watched:** `config/boards.yaml`

## Step 1: Check the live boards on your Mac (about 2 minutes, no accounts)

```
cd job-radar
python3 -m venv .venv && source .venv/bin/activate
pip install -r collector/requirements.txt
python -m collector --dry-run
```

This prints which boards respond and your 40 newest matches. Nothing is saved. A few wrong company names are expected and harmless.

## Step 2: Put the code on GitHub

Create a new **public** repository named `job-radar` and push this folder to it. Public repositories get unlimited Actions minutes. The repository holds your matching rules and company list, but not your tracker data, password, or keys.

## Step 3: Create the database

1. Create a free project at supabase.com.
2. Click **Connect**, then copy the **Transaction pooler** connection string and put your database password in it. If the password has symbols like `@` or `/`, reset it to letters and numbers.
3. In GitHub, go to **Settings > Secrets and variables > Actions** and add a secret named `DATABASE_URL` with that string.

## Step 4: Start collecting

In GitHub, open **Actions > Collect jobs > Run workflow**. When it finishes with a green check, the schedule is running. The tables are created automatically.

## Step 5: Put the dashboard online

1. At vercel.com, choose **Add New > Project** and import the repository. Leave the framework preset on **Other**.
2. Add these environment variables, then deploy:
   - `DATABASE_URL`: the same string as above
   - `DASHBOARD_PASSWORD`: a long password you choose
   - `SESSION_SECRET`: the output of `openssl rand -hex 32`

Open the site on your phone or computer and sign in.

## Step 6: Add the job sites

These cover companies that are not on your list. Both are off until their key is added.

**LinkedIn and Indeed through Apify (paid):** add a GitHub secret named `APIFY_TOKEN`. One token covers both. These read logged-out pages only, and both sites' terms may prohibit scraping, so I have not checked Indeed's. Never add your LinkedIn or Indeed login to this project.

**Google for Jobs through JSearch (free tier):** subscribe to the free Basic plan of JSearch on RapidAPI, then add a GitHub secret named `RAPIDAPI_KEY`.

## Step 7: Turn on the Check now button

1. In GitHub, go to **Settings > Developer settings > Personal access tokens > Fine-grained tokens** and generate a token. Limit it to the `job-radar` repository, and give it only the **Actions: Read and write** permission.
2. In Vercel, add `GITHUB_DISPATCH_TOKEN` (that token) and `GITHUB_REPO` (`your-username/job-radar`), then redeploy.

## Optional: LinkedIn alert emails (free)

1. Create a new Gmail account just for this. Turn on 2-Step Verification, then create an **App password**.
2. Create LinkedIn job alerts for your target titles, at the most frequent setting offered.
3. In the inbox that receives those alerts, add a filter that auto-forwards mail from `jobalerts-noreply@linkedin.com` to the new Gmail. Forward with a filter, not by hand, so the original sender is kept.
4. Add GitHub secrets `ALERT_EMAIL_USER` (the new address) and `ALERT_EMAIL_APP_PASSWORD`.

## When each source runs

| Source | When | Cost |
|---|---|---|
| Company career pages | Every 15 minutes | Free |
| LinkedIn alert emails | Every 15 minutes | Free |
| LinkedIn through Apify | Midnight, 6am, noon, 6pm New York time, plus Check now | $0.002 per result, about $17 a month at most |
| Indeed through Apify | Same times | $0.0001 per result, about $5 a month at most |
| Google for Jobs through JSearch | Same times, one title per run in turn | Free, 200 requests a month |

- **Each search continues from the last one.** It looks back to that title's last successful search, plus 30 minutes in case a job was listed late. If a search fails or is skipped, the next one covers the gap, up to 7 days.
- **Check now** runs every free source right away. Job-site searches run too, unless they ran in the last 30 minutes.
- **Limits:** LinkedIn stops at 288 results a day and Indeed at 1,600, each including Check now. Indeed only accepts 1, 3, 7, or 14 day windows, so it may return jobs already collected. Those are recognized and not repeated. Indeed listings that hide the employer's name are skipped. JSearch keeps enough requests for the month's scheduled runs, so Check now uses only the spare ones.
- **If a search fills its 18 results,** the dashboard warns that some roles may be missing. Raise `per_search_limit` in `config/boards.yaml`, and raise `daily_result_budget` to match if needed.

## Changing what it collects

- **Add a company:** add its slug to `config/boards.yaml`. The slug is the last part of its board address, such as `stripe` in `boards.greenhouse.io/stripe`.
- **Change matching:** edit `config/profile.yaml`. Scores update on the next run.
- **Teach it:** marking a role **Not a fit** lowers future titles that keep sharing the same words.

## How freshness works

Jobs are ordered by the employer's own publish date when one exists. Greenhouse may only provide a last-edited date, so those jobs count from when the collector first saw them. The first time a board is added, its existing jobs keep their real dates and do not appear as new. Jobs older than 21 days are ignored when first found.

## If something looks wrong

The dashboard shows when collection last ran and lists any board that is not responding. Workday's listing format is undocumented, so those boards are the most likely to need attention. One failing board never stops the others.

## Tests

```
pip install pytest
pytest tests/test_units.py
```

The full suite also needs an empty UTF-8 Postgres database in `TEST_DATABASE_URL`, and it erases that database.
