# Walang Pasok API

**Is there class tomorrow?** A public API and website that tracks class suspensions
("walang pasok") in the Philippines caused by typhoons, floods, holidays and local events.

Today this information lives in news articles and Facebook posts from LGUs. Walang Pasok
turns it into structured, verified data that apps, Discord bots and school websites can query:

```bash
curl "http://localhost:8000/api/v1/check?location=angeles-city&level=college"
```
```json
{ "suspended": true, "location": { "slug": "angeles-city", ... }, "date": "2026-10-11",
  "details": [ { "reason": "weather", "reason_detail": "Tropical Storm Signal No. 2", ... } ],
  "sources": [ { "name": "Angeles City Information Office", "url": "https://..." } ] }
```

**Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL · Jinja2 · Pytest

---

## Running locally

### 1. Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # Windows: copy .env.example .env
```

Set `ADMIN_API_KEY` in `.env` to a secret of your own.

### 2. Start a database

**PostgreSQL (recommended):** the default `DATABASE_URL` in `.env.example` points here.

```bash
docker compose up -d db
```

**No Docker?** Set `DATABASE_URL=sqlite:///./walang_pasok.db` in `.env`. The models and
migrations are database-agnostic, so SQLite works for trying the project out.

### 3. Migrate, seed, run

```bash
alembic upgrade head
python -m scripts.seed          # 42 locations + 45 real suspensions from real articles (re-run safely)
uvicorn app.main:app --reload
```

| URL | What |
|---|---|
| http://localhost:8000 | Website: "May pasok ba bukas?" |
| http://localhost:8000/docs | Interactive API docs (Swagger UI) |
| http://localhost:8000/redoc | API reference (ReDoc) |

The seed imports **real suspensions from real articles** (Inquirer, GMA News, The Summit Express
and the Quezon City government; see `scripts/real_examples.py`). They're in the past, so open a
past day with `?date=`:

- http://localhost:8000/?date=2025-07-21&q=pasig-city (habagat, July 2025)
- http://localhost:8000/?date=2026-10-05&q=santa-rita-pampanga (National Teachers' Day, face-to-face only)
- http://localhost:8000/?date=2025-10-20&q=angeles-city (Tropical Storm Ramil)

The homepage also links to recent days that had suspensions. For fictional samples dated
today and tomorrow, run `python -m scripts.seed --samples`.

### Tests

```bash
pytest
```

Tests run against in-memory SQLite with "today" frozen, so they need no database or setup.

---

## API

Base path: `/api/v1`. All responses are JSON. Public endpoints return **verified**
suspensions only. "Today" always means today in **Asia/Manila**, regardless of server timezone.

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/check` | | Yes/no answer for a location, day and level |
| GET | `/suspensions` | | Filterable, paginated list |
| GET | `/suspensions/today` | | Today's suspensions nationwide |
| GET | `/suspensions/{id}` | | One suspension |
| GET | `/locations` | | Search locations to find their slugs |
| POST | `/suspensions` | admin | Report a suspension (starts as `pending`) |
| POST | `/suspensions/{id}/sources` | admin | Add another source as proof (e.g. a news article) |
| PATCH | `/suspensions/{id}/verify` | admin | Mark verified so it becomes public, and send alerts |
| POST | `/imports` | admin | Read suspensions from article text (dry run by default) |
| POST | `/alerts/discord` | | Send alerts to a Discord channel |
| DELETE | `/alerts/{manage_token}` | | Turn an alert off |

### Examples

**Are classes suspended?** `level`, `school_type` and `date` are optional (`date` defaults to today).

```bash
curl "http://localhost:8000/api/v1/check?location=quezon-city"
curl "http://localhost:8000/api/v1/check?location=angeles-city&level=college&date=2026-10-11"
curl "http://localhost:8000/api/v1/check?location=san-fernando-pampanga&school_type=public"
```

**List suspensions**, filtered and paginated:

```bash
curl "http://localhost:8000/api/v1/suspensions?date=2026-10-11&level=elementary"
curl "http://localhost:8000/api/v1/suspensions?location=marikina-city&page=1&size=10"
curl "http://localhost:8000/api/v1/suspensions/today"
```

List responses are pages:

```json
{ "items": [ ... ], "total": 42, "page": 1, "size": 20, "pages": 3 }
```

**Find a location's slug:**

```bash
curl "http://localhost:8000/api/v1/locations?search=san%20fernando"
```

**Report and verify a suspension (admin):**

```bash
curl -X POST "http://localhost:8000/api/v1/suspensions" \
  -H "X-API-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{
        "location": "pasig-city",
        "date": "2026-10-12",
        "levels": ["all"],
        "reason": "weather",
        "reason_detail": "Signal No. 2",
        "source_url": "https://www.facebook.com/PasigCity/posts/...",
        "source_name": "Pasig City PIO"
      }'

curl -X PATCH "http://localhost:8000/api/v1/suspensions/1/verify" -H "X-API-Key: $ADMIN_API_KEY"
```

**Attach proof.** Every suspension's `proof` list starts with its official source. Add the
announcement text and post time when you create it, and add news reports later:

```bash
curl -X POST "http://localhost:8000/api/v1/suspensions" \
  -H "X-API-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{
        "location": "angeles-city", "date": "2026-10-12", "levels": ["all"], "reason": "weather",
        "source_url": "https://www.facebook.com/.../posts/...",
        "source_name": "Angeles City Information Office",
        "source_excerpt": "WALANG PASOK: Classes at all levels are suspended tomorrow...",
        "source_posted_at": "2026-10-11T20:05:00+08:00"
      }'

curl -X POST "http://localhost:8000/api/v1/suspensions/1/sources" \
  -H "X-API-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"url": "https://news.example.ph/walang-pasok", "name": "Example News", "kind": "news_article"}'
```

`kind` is `facebook_post`, `x_post`, `news_article`, `official_website` or `other`; leave it
out and it's guessed from the URL. On the website, the **Show proof** buttons open these
as cards with the quoted text and a link to the original.

On Windows PowerShell, use `curl.exe` and put the JSON body in a file (`-d "@body.json"`).

### Errors

Every error has the same shape:

```json
{ "error": { "code": "not_found", "message": "Location 'atlantis' not found.", "details": {"param": "location"} } }
```

| Status | `code` | When |
|---|---|---|
| 400 | `bad_request` | Well-formed but invalid input, e.g. an unknown location slug as a filter or in a new suspension |
| 401 | `unauthorized` | Admin endpoint without a valid `X-API-Key` |
| 404 | `not_found` | Unknown suspension ID, or unknown location in `/check` |
| 422 | `validation_error` | Wrong types or values (bad date, unknown level, page size over 100); `details` lists each problem |

---

## Getting real suspensions in

### The importer

LGUs and news sites post lists like `Angeles City – all levels, public and private`.
`app/services/importer.py` reads them. It finds the place (province headings and
"Place, Province" tell same-named towns apart), the levels ("kindergarten to senior high"),
public/private, face-to-face only, morning/afternoon and "until Friday, Sept. 11". It saves
each line as the proof quote. Lines with places it doesn't know are listed as `unmatched`.
Exceptions ("except Concepcion Integrated School") and extra dates get flagged for review.
Lines about particular schools ("San Simon: The following schools are switching to online
classes") aren't treated as a whole-town suspension; they're listed for a person to check.
It's rule-based on purpose: predictable, tested and free to run every few minutes.

Headings matter. "San Fernando" under a **Pangasinan** heading is not San Fernando, Pampanga,
and "Santo Tomas" under Pangasinan is not Santo Tomas, Pampanga, so those are left unmatched
instead of guessed. The tests in `tests/test_real_articles.py` and `tests/test_live_snapshot.py`
run the importer on full text from live GMA, Inquirer and Summit Express pages, traps included.

```bash
# A news article or LGU site (date read from the headline). Shows what it found; saves nothing:
python -m scripts.import_article https://newsinfo.inquirer.net/2301928/walang-pasok-class-suspensions-on-thursday-sept-10

# Save as pending (--save), or make public and send alerts right away (--save --verify):
python -m scripts.import_article https://newsinfo.inquirer.net/... --save

# An announcement written as a paragraph (like an LGU's own post): say which place it covers
python -m scripts.import_article https://quezoncity.gov.ph/... --place metro-manila --kind official_website

# Facebook posts can't be fetched. Copy the post text into a file:
python -m scripts.import_article "https://www.facebook.com/.../posts/..." --file post.txt \
  --date 2026-10-12 --source-name "Angeles City Information Office" --save
```

The same thing over HTTP is `POST /api/v1/imports`. If a place already has a suspension that
day, a second article is added to its **proof** instead of creating a duplicate.

### The watcher

The watcher checks news pages and imports new suspension articles. **It runs by itself**: the
web app starts it in the background and repeats it every `WATCH_INTERVAL_MINUTES` (15), and in
production GitHub Actions runs it (below). It remembers what it has read in the
`watched_pages` table, so repeated runs only do new work, and it ignores articles about days
already past. It identifies itself, obeys robots.txt and waits between requests. Sources are
listed in `app/services/watcher.py`:

| Source | How it's read |
|---|---|
| [GMA News](https://data.gmanetwork.com/gno/rss/serbisyopubliko/feed.xml) | Serbisyo Publiko RSS; the article body comes from the page's schema.org JSON-LD (`articleBody`) |
| [Guro Tayo](https://www.gurotayo.com/) | Blogger RSS; body from `[itemprop=articleBody]`; roundups written as numbered sections |
| [Inquirer](https://newsinfo.inquirer.net/tag/walang-pasok/feed) | RSS; body from `#article_content`. Currently answers 403 to automated readers |

Only the article body is read, so sidebars and "read next" headlines are never mistaken for
suspensions. Sites that refuse automated readers (Manila Bulletin, ABS-CBN, The Summit Express's
robots.txt) are left out on purpose; the watcher never disguises itself.

**LGU posts on Facebook and X.** News lists usually link each place to the LGU's own post. When a
line's place name links to Facebook, X or a `.gov.ph` site, that post becomes the suspension's
**official source**, with the article as supporting proof. Facebook itself is never scraped: reading
other Pages through Meta's API needs Page Public Content Access (an app review), and scraping
breaks Facebook's terms. For an LGU post no news site has picked up, use
`scripts.import_article --file` (below).

**Roundups written as sections** ("1. San Jose del Monte City, Bulacan — October 12–14, 2026" and a
paragraph) only count when the paragraph says classes are suspended. A special non-working day on its
own, or "does not ... suspension of classes", goes to a person instead. Date ranges in the heading
become "until lifted" with the right resume day.

**Places.** Every region, province, city and municipality in the PSGC (1,732 places) is loaded from
the snapshot in `app/data/psgc/`. Same-named towns are told apart by the province written next to
them or the heading they're under; when that isn't possible the line is left for a person, never
guessed. Refresh the snapshot with `python -m scripts.seed --locations-only --refresh-psgc`.

**Corrections are caught.** News lists get edited after publishing. Inquirer's Oct. 5, 2026
list first included Angeles City, then an erratum removed it. When an article the watcher
already read changes, anything it no longer lists is reported to the admin channel as
"no longer listed in its source" for a person to check.

Not covered yet: announcements written only as prose, and LGU posts no news site has linked to.
Use `scripts.import_article --place` or `--file` for those.

### Hands-off publishing

Each watcher cycle (`app/services/autopilot.py`) publishes a find on its own, and sends the
Discord and Facebook alerts, when it is well-sourced and the importer raised no warnings:

- **It links to the LGU's own post** (Facebook, X or a `.gov.ph` page), or
- **two different news outlets** list it (it is published on the cycle where the second one appears).

Everything else stays **pending** and is listed in `ADMIN_DISCORD_WEBHOOK_URL` with the reason:
a single report with no LGU link, a line with exceptions or an unreadable "until" date, or a place
its source article later dropped. A wrong "walang pasok" is worse than a late one, so those wait for
a person (`PATCH /api/v1/suspensions/{id}/verify`). `AUTO_PUBLISH=false` holds everything for review.

`python -m scripts.watch` runs one cycle by hand, which you shouldn't normally need.

### Running it 24/7 for free: GitHub Pages + GitHub Actions + Neon

The whole service runs on GitHub, with no server of your own. `.github/workflows/site.yml`
(**Update site**) runs every 15 minutes:

1. checks the news and publishes well-sourced suspensions (hands-off publishing, above),
   sending Discord/Facebook alerts and the admin summary;
2. rebuilds the website and JSON API as static files (`scripts/build_site.py`);
3. deploys them to **GitHub Pages**.

GitHub Pages only serves files, so the static site is the same pages rendered ahead of time:
one page per city or municipality (`/p/angeles-city/`), and the API as JSON files
(`/api/v1/check/angeles-city.json`, `/api/v1/suspensions/today.json`, `/api/v1/locations.json`),
documented at `/api/` on the site. Query filters (`?level=college`) and the Discord sign-up form
need the server, so they're only in the self-hosted version (`uvicorn`, or `render.yaml`).

**Admin without a server:** **Actions → Admin → Run workflow** lists pending suspensions,
verifies them by id, or imports an announcement you've read yourself (paste an LGU Facebook post's
link and text). The site is rebuilt right after.

**Setup.** Shortcut (Windows): `powershell -ExecutionPolicy Bypass -File scripts\setup_github.ps1`
does all of it with [GitHub CLI](https://cli.github.com). By hand:

1. Create a free Postgres database on [Neon](https://neon.tech) and copy its connection
   string (`postgresql://…`; the app adds the driver name itself).
2. Push this project to a GitHub repository.
3. **Settings → Pages → Source: GitHub Actions.**
4. **Settings → Secrets and variables → Actions**
   - Secret `DATABASE_URL`: the Neon connection string
   - Secret `ADMIN_DISCORD_WEBHOOK_URL` (optional): what was published and what needs review
   - Secrets `FACEBOOK_PAGE_ID` and `FACEBOOK_PAGE_ACCESS_TOKEN` (optional): post published suspensions to your Page
5. **Actions → Update site → Run workflow** (tick "load the real examples" the first time).
   The site is live at `https://<you>.github.io/<repo>/` a few minutes later.

GitHub starts scheduled runs late when it's busy (sometimes by an hour or more), and in public
repos it switches the schedule off after 60 days without commits (turn it back on in the Actions
tab). `.github/workflows/tests.yml` runs the test suite on every push. Copies of the workflows
are kept in `deploy/github-workflows/`.

---

## Alerts: Discord and Facebook

When a suspension is verified **for the first time**, Walang Pasok tells:

- every **Discord channel** following that place, and
- your **Facebook Page**, if you've set one up.

Messages go out after the verify response is sent (FastAPI background task), so verifying
stays fast. Only suspensions for today or later (or ongoing "until lifted" ones) are announced.

### Discord

Anyone can sign up a channel from the website's **Get alerts** form, or through the API:

```bash
curl -X POST "http://localhost:8000/api/v1/alerts/discord" -H "Content-Type: application/json" \
  -d '{"webhook_url": "https://discord.com/api/webhooks/…", "location": "angeles-city", "label": "AUF CCS"}'
```

To get a webhook URL in Discord: **Server Settings → Integrations → Webhooks → New Webhook →
Copy Webhook URL.** A welcome message is posted right away (`"send_test": false` skips it).

- **Who hears what:** followers of a city hear about that city and anything wider that covers
  it (a Metro Manila-wide suspension reaches Quezon City followers). Followers of a province
  also hear about cities inside it. No location means nationwide. Same hierarchy as `/check`.
- **The webhook URL is a secret** (it lets anyone post to that channel), so it's never returned
  by the API or written to logs. Only real `discord.com/api/webhooks/…` URLs are accepted.
- **Turning it off:** sign-up returns a `manage_token`, shown once. Use it with
  `DELETE /api/v1/alerts/{manage_token}` or the form on the result page. Deleting the webhook in
  Discord also works: when Discord answers 401/403/404 the alert switches itself off. It also
  switches off after 5 failures in a row.
- Messages never ping `@everyone`, whatever an announcement says.

### Facebook

Walang Pasok can **post to your own Page**. It doesn't read other Pages' posts: reading LGU
pages through Meta's API needs a permission that goes through Meta's app review, and scraping
Facebook breaks its terms. LGU posts get in through the admin endpoints instead, with the link
saved as proof.

To turn on auto-posting:

1. Create a Facebook Page for the project, and an app at [developers.facebook.com](https://developers.facebook.com).
2. Get a **Page access token** with the `pages_manage_posts` permission (the Graph API Explorer
   can generate one; exchange it for a long-lived token for production).
3. Set `FACEBOOK_PAGE_ID` and `FACEBOOK_PAGE_ACCESS_TOKEN` in `.env`, and `PUBLIC_BASE_URL`
   to your site's address so posts link back to it.

Meta changes its permissions, review rules and Graph API versions often (`FACEBOOK_GRAPH_VERSION`,
default `v23.0`), so check their current docs when setting this up.

---

## Data model

Suspensions vary a lot (one city vs. a whole province, college only vs. all levels,
afternoon only, private schools only, "until further notice"), so the model is explicit
about each of these instead of using free text.

```
locations                      suspensions                         suspension_levels
─────────                      ───────────                         ─────────────────
id                             id                                  id
psgc_code  (unique)       ┌──  location_id ──────────────┐         suspension_id ──→ suspensions
name                      │    date                      │         level
slug       (unique)       │    school_type               │         (unique per suspension)
type                      │    mode                      │
region                    │    time_scope                │
province                  │    until_lifted, lifted_on   │
parent_id ──→ locations   │    reason, reason_detail     │
                          │    source_url, source_name   │
                          │    source_kind, _excerpt,    │
                          │    _posted_at                │
                          │    status, verified_at       │
                          │    created_at, updated_at    │
```

Two more tables: **`suspension_sources`** holds extra proof for a suspension (news articles
and other posts that back up the official one), and **`subscriptions`** holds Discord alerts
(webhook URL, place followed, manage token, failure count).

### Location

Identified by its **PSGC code** (the Philippine Standard Geographic Code from the PSA,
10-digit format) and by a URL-friendly **slug** used in the API (`angeles-city`, `quezon-city`).
A slug is the slugified name, with the province appended when the name isn't unique
nationwide (`san-fernando-pampanga`, `santa-ana-pampanga`).

`type` is `region`, `province`, `city` or `municipality`. `parent_id` decides which wider
announcements apply to a place:

- A **component city or municipality** (San Fernando, Mabalacat, Porac) has its **province** as parent.
- A **highly urbanized city** (Angeles, every NCR city) is politically independent of its
  province, so its parent is the **region**.

So a **Pampanga-wide suspension covers San Fernando but not Angeles City**, which matches
how suspensions are actually declared. A region-wide one (e.g. all of Metro Manila) covers
every city in the region. `/check` and the `location` filter use this hierarchy.

`province` is kept separately as the *geographic* province (Angeles shows "Pampanga"), for display and search.

### Suspension

| Field | Values | Notes |
|---|---|---|
| `location` | Location | Can be a city, province or region |
| `date` | date | First affected day |
| `levels` | `preschool`, `elementary`, `junior_high`, `senior_high`, `college`, `all` | One or more; `all` absorbs the rest |
| `school_type` | `public`, `private`, `both` | |
| `mode` | `all_classes`, `face_to_face_only` | Face-to-face only means online/modular classes may continue |
| `time_scope` | `whole_day`, `morning`, `afternoon` | |
| `until_lifted` | bool | Applies from `date` onward until `lifted_on` (the day classes resume) is set |
| `reason` | `weather`, `holiday`, `event`, `other` | Plus free-text `reason_detail` |
| `source_url`, `source_name` | | Where the announcement came from; required |
| `status` | `pending`, `verified` | New reports start `pending`; only `verified` is public |

**Matching rules** (in `app/services/suspensions.py`):

- **Level:** asking about `college` matches suspensions for `college` **or** `all`.
- **School type:** asking about `public` matches `public` **or** `both`.
- **Date:** a suspension covers a day if it's on that day, **or** it's `until_lifted`,
  started on or before that day, and hasn't been lifted yet.
- **Location:** a place is covered by its own suspensions and its parents' (see above).
- `/check` returns `suspended: true` for partial suspensions too (afternoon-only,
  face-to-face only). Read `details` for the scope.

### Design notes

- **Levels live in their own table**, not an array column. Level filtering stays a plain
  SQL join, and the schema works on PostgreSQL and SQLite alike.
- **Enums are stored as strings**, not native PostgreSQL enums. Adding a new `reason`
  is a code change, not a migration.
- **"Today" goes through one function** (`app/services/clock.py`) and one FastAPI dependency
  (`get_today`), so tests can freeze time and a UTC server never gets the day wrong.

---

## Project structure

```
app/
  main.py            App factory: routers, error handlers, gzip
  config.py          Settings from environment / .env
  db.py              Engine and session dependency
  errors.py          Domain errors and the uniform error response
  models/            SQLAlchemy models (Location, Suspension, SuspensionLevel)
  schemas/           Pydantic request/response models
  services/          Business logic: filtering, /check, create/verify, alerts, importer, watcher
  api/deps.py        Shared dependencies: DB session, today, pagination, admin key
  api/v1/            Versioned routers: suspensions, check, locations, alerts
  web/               Website routes and the weekly timetable (server-rendered, no JavaScript)
  templates/         Jinja2 templates + inlined CSS
alembic/             Migrations
scripts/seed.py      Seed locations and the real examples
scripts/import_article.py   Import one article or post
scripts/watch.py     The news watcher (GitHub Actions runs it)
.github/workflows/   Watcher schedule and test runs
tests/               Pytest suite
```

Routers stay thin: they parse the request, call a service, and shape the response.
Logic lives in `services/` so other entry points can reuse it.

## Roadmap

The structure leaves room for these, which are not built yet:

- **An admin page** for reviewing and verifying pending suspensions (today it's the API docs).
- **More watcher sources** and **auto-verify** when two independent sources agree.
- **A full Discord bot** with slash commands like `/pasok angeles-city`, built on the public API.
- **Redis caching** for `/check` and `/suspensions/today`, invalidated on verify.
- **Deployment** to Render with Neon Postgres. The `Dockerfile` already runs migrations on start and honours `$PORT`.
- Admin endpoints to edit and lift suspensions (`lifted_on`), and more provinces in the seed data.

## Data accuracy

Seed PSGC codes follow the PSA's 10-digit format. Verify them against the latest
[PSGC publication](https://psa.gov.ph/classification/psgc) before relying on them.
Always confirm suspensions with the official LGU or school announcement.
