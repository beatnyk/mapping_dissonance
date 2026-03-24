# Changelog

## 2026-03-24 — Pre-deploy Cleanup

- `app.py` — Removed duplicate `@app.route("/robots.txt")` definition (shadowed second handler deleted).
- `app.py` — Removed dead `/api/recording-pins` endpoint (never called from any template); removed orphaned `_IUCN_RANK` and `_IUCN_LABEL` dicts that existed only to serve it.
- `app.py` — Fixed 3 bare `except:` clauses → `except Exception:` (lines: `get_coords`, `_fetch_rss`, `get_iucn_status`).
- `Procfile` — Changed `--workers 2` → `--workers 1` to match Dockerfile and prevent in-memory cache fragmentation.

## 2026-03-24 — Mediastack News Integration

- `app.py` — Replaced NewsAPI (TIER 3) with Mediastack REST API. Same environmental keyword set retained; query mapped to Mediastack `keywords`/`countries`/`languages` params. No new library dependency — uses `requests` already in the stack.
- `requirements.txt` — Removed `newsapi-python==0.2.7`.
- `.env.example` — Renamed `NEWS_API_KEY` → `MEDIASTACK_API_KEY`.
- `render.yaml` — Updated env var key to `MEDIASTACK_API_KEY`.
- `templates/bibliography.html` — Updated news source attribution to Mediastack.

## 2026-03-23 — Production Hardening

### Security
- `.env` — Removed all live credentials. File now contains placeholder values only. Added `.env.example` with documentation for every required variable.
- `templates/base.html` — Added `<meta name="csrf-token">` CSRF token tag. Consumed by all JS `fetch()` POST calls via `getCsrfToken()` helper.
- `templates/login.html`, `templates/register.html` — Added `<input type="hidden" name="csrf_token">` to both auth forms.
- `templates/archive.html` — Added `csrf_token` hidden inputs to upload and folklore submission forms. Added `X-CSRFToken` header to all three `fetch()` POST calls (`/get_context_news_batch`, `/care/sign/`, `/folklore/witness/`). Added `getCsrfToken()` JS helper.
- `app.py` — Added `Flask-WTF` `CSRFProtect(app)`. All POST routes now require a valid CSRF token; `/get_context_news_batch` (read-only) is exempted.
- `app.py` — Added `Flask-Limiter` rate limiting: `/login` 20/hr, `/register` 5/hr, `/upload` 10/hr, `/care/sign` 30/hr, `/folklore/submit` 5/hr.
- `app.py` — Added `@app.after_request` security headers: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`.
- `app.py` — Replaced all `print(f"[WARNING] ...")` with `logger.warning(...)` (structured logging via Python `logging` module).

### Infrastructure
- `.dockerignore` — New file. Excludes `.venv/`, `__pycache__/`, `instance/`, `static/uploads/*`, `.env`, `.git/`, `.DS_Store`, `*.md` from Docker build context. Estimated image size reduction: ~500 MB.
- `Dockerfile` — Workers aligned to 1 (was 2). Added `libpq-dev` for psycopg2. Added `setup_birdnet.py` run during build. Timeout kept at 120s.
- `render.yaml` — Timeout corrected from 60s → 120s (accommodates BirdNET cold start). Python version corrected to 3.12.0. Added `IUCN_TOKEN`, `ALGORAND_MNEMONIC`, `SENTRY_DSN`, `DATABASE_URL` as `sync: false` env vars. Added second disk mount for `instance/` (1 GB) so SQLite database persists across redeploys.

### Monitoring
- `app.py` — Added optional Sentry integration via `sentry-sdk[flask]`. Initialised only when `SENTRY_DSN` env var is set; disabled otherwise (no-op).
- `app.py` — `logging.basicConfig` with timestamp + level + name format. `logger = logging.getLogger("mapping_dissonance")`.

### Reliability
- `app.py` — `DATABASE_URL` `postgres://` → `postgresql://` rewrite handles Render/Heroku legacy URL prefix that SQLAlchemy 2.x rejects.
- `app.py` — `DATABASE_URL` falls back to SQLite when env var is empty or unset (safe for local dev).
- `requirements.txt` — Added `Flask-WTF==1.2.2`, `Flask-Limiter==3.9.0`, `sentry-sdk[flask]==2.22.0`, `gdeltdoc==1.4.0`, `psycopg2-binary==2.9.10`.
- `app.py` — `gdeltdoc` GDELT tier-2 news path now has its import inside a `try/except`, making it properly optional.

### New Endpoints
- `GET /health` — Liveness probe. Returns `{"status": "ok", "birdnet": bool, "algorand": bool}`. Used by Render health checks.
- `GET /robots.txt` — Disallows crawling of `/upload`, `/care/`, `/folklore/`.

### Cache
- `app.py` — Audio files served at `/audio/<filename>` now receive `Cache-Control: public, max-age=31536000, immutable` (1-year cache; filenames are timestamp-prefixed and content-addressed).
- `app.py` — Static files at `/static/` receive `Cache-Control: public, max-age=86400` (1-day cache).
- `app.py` — All other responses receive `Cache-Control: no-cache`.

## 2026-03-20

### Added
- `Dockerfile` — builds the app with `python:3.12-slim`, installs dependencies, exposes port 80, runs gunicorn. Port 80 is intentional for Dokploy/Traefik compatibility (same principle as sftp-manager).
- `.dockerignore` — excludes `venv/`, `__pycache__/`, `instance/`, `static/uploads/`, `.env`, `.git/` from the Docker build context.
- `README.md` — covers project overview, stack, local dev setup, environment variables, and deployment summary.

### Changed
- `app.py` — moved `db.create_all()` to module level (outside `if __name__ == '__main__'`) so the database tables are created on startup when running under gunicorn, not just when invoking the script directly.

## 2026-03-22

### Changes made by beatnyk

No.	File	Change
1	app.py:97	Removed wait_for_confirmation() — Algorand no longer blocks 4+ seconds per signature
2	app.py:172	Added timeout=5 to Nominatim geocoding — won't hang upload requests
3	app.py:923	News cache evicts expired entries on each insert — no more unbounded memory growth
4	app.py:891	Ledger query capped at .limit(200) — prevents full-table join on page load
5	app.py:122,135,136	Added index=True to ArchiveEntry.timestamp, CareSignature.entry_id, .user_id
5b	app.py:1232	Startup CREATE INDEX IF NOT EXISTS SQL — applies indexes to already-deployed DBs without a migration
6	render.yaml:6	Workers 2→4, timeout 120→60 — 2× concurrency, faster fail-fast on hung requests
7	base.html:9	Google Fonts loads async via preload + onload swap — page renders immediately
8	archive.html:5, landing.html:5	MapLibre CSS loads async the same way — maps no longer block first paint

## 2026-03-22 — Performance & Security Audit Pass 2

### Security
- [G] **XSS fix** `archive.html` — `{{ entries_json | safe }}` → `{{ entries_json | tojson }}`. User-submitted location/species fields were injected raw into a `<script>` block; `tojson` applies proper JSON-safe escaping.

### Performance — Backend
- [D] **BirdNET lazy init** `app.py` — Moved `Analyzer()` from module level into `upload()`. Previously all 4 Gunicorn workers each loaded the ~400 MB TensorFlow model at startup. Now loads on first upload only, on the worker that handles it.
- [C] **Batch news endpoint** `app.py` — Extracted news-fetching logic into `_fetch_news_payload(location)`. Added `/get_context_news_batch` (POST) that fetches all locations in parallel. Archive page now makes 1 request instead of N requests staggered 400 ms apart.
- [F] **Gzip compression** `app.py`, `requirements.txt` — Added `Flask-Compress`. All HTML, CSS, JS, and JSON responses automatically compressed (~60–70% smaller).

### Performance — Frontend
- [A] **Removed duplicate @import** `static/style.css` — Deleted line 1 `@import url(https://fonts.googleapis.com/...)`. Duplicate of the async preload already in `base.html`; was firing a redundant render-blocking request on every page.
- [H] **Replaced Three.js + React** `about.html` — Removed 670 KB Three.js and ~130 KB React/ReactDOM CDN dependencies. Replaced with ~60 lines of vanilla JS: Canvas 2D particle field (identical visual: 320 drifting points, mouse parallax, theme reactivity, wrap-around edges) and plain DOM for the section progress nav.

### Assets
- [B] **Deleted unused font** `static/fonts/ZalandoSans-variable.woff2` (205 KB) — Never referenced in any CSS rule.

## 2026-03-22 — Care Receipt, Narrative Update, BirdNET + IUCN Pipeline

### Feature: Care Signature Receipt
- `app.py` — `CareSignature` flush before commit to obtain `sig.id`; response now returns `care_id` alongside `care_token`.
- `app.py` — New `UserTransactionLog` model: stores `user_id`, `care_signature_id` (FK), `txid`, `timestamp`. A permanent per-user log of every Transaction ID and Care Ledger ID issued. Populated on every successful care signing.
- `app.py` — Startup SQL adds indexes on `user_transaction_log(user_id)` and `user_transaction_log(care_signature_id)`.
- `archive.html` — Sign button replaced post-signing with an inline receipt panel showing: Transaction ID (full token, selectable), Care Ledger ID, a save notice, and a "View Ledger →" button.
- `static/style.css` — `.care-receipt` block: mono IDs with `user-select: all`, dark background button.

### Content: Landing Page Node Narratives
- `templates/landing.html` — Full narrative text replaced on three static nodes:
  - **Mangar Bani (Chapter I)** — Lohan at the grove, the merchant's axe, the Dhau tree bleeds iron, forty-day vigil.
  - **Tughlaqabad (Chapter II)** — Lohan as stone-cutter, Sultan vs. Nizamuddin, the oil ban, water burning with jasmine flame, the abandoned fort.
  - **Bhati Mines (Chapter III)** — The machines, dust in the bread, toxic sapphire lake, the boy who dived and did not come up.
  - Strings switched to template literals to cleanly handle apostrophes and dialogue quotes; paragraphs separated with `<br><br>`.

### Feature: BirdNET Identification + IUCN Pipeline

#### Root causes fixed
- `birdnetlib` was commented out in `requirements.txt` — `BIRDNET_AVAILABLE` was always `False`.
- `Recording()` was called without `lat`/`lon`/`date` — BirdNET uses location + season to filter candidate species; omitting them degrades accuracy.
- `get_coords()` was called *after* BirdNET analysis — coordinates were never available for the first analysis pass.
- No `iucn_status` stored on `ArchiveEntry` — IUCN was never persisted; `entry_to_dict` returned no `iucn` field, so all map pins defaulted to LC.
- `lng=` passed to `Recording()` — the actual parameter name is `lon=`; caused a `TypeError` that the silent `except: pass` swallowed.
- Location filter sets `analyzer.custom_species_list` globally — persisted across Recording instances; the global-fallback pass reused the filtered list and found nothing.
- `tflite-runtime` unavailable on Python 3.12 — installed `ai-edge-litert` (Google's successor); created `tflite_runtime` compatibility shim.
- Missing transitive dependencies: `librosa`, `resampy` not declared in `requirements.txt`.

#### Changes
- `requirements.txt` — Added `birdnetlib==0.18.0`, `librosa==0.11.0`, `resampy==0.4.3`, `ai-edge-litert==2.1.3`, `tflite-runtime` (Python < 3.12 only).
- `setup_birdnet.py` — New script: checks for `tflite_runtime`; if absent, writes a compatibility shim pointing to `ai_edge_litert.interpreter`. Safe no-op if `tflite-runtime` is already installed (Python 3.11 server).
- `render.yaml` — `buildCommand` now runs `python setup_birdnet.py` after `pip install`.
- `app.py` — `get_coords()` moved to before BirdNET analysis so `lat`/`lng` are available.
- `app.py` — `Recording()` now receives `lat=`, `lon=`, `date=`, `min_conf=0.1`.
- `app.py` — Two-pass analysis: pass 1 uses location-filtered species list (region-aware); if no detections, resets `analyzer.custom_species_list = []` / `analyzer.has_custom_species_list = False` and runs pass 2 with global species list.
- `app.py` — After identification: looks up IUCN code from `_BIRD_IUCN` (common name) then `_SCI_IUCN` (scientific name); falls back to live IUCN API call for unknown species; stores result as `iucn_status` on `ArchiveEntry`.
- `app.py` — `_SCI_IUCN` dict added alongside `_BIRD_IUCN` (scientific name → IUCN code).
- `app.py` — `iucn_status` column added to `ArchiveEntry` model; startup SQL runs `ALTER TABLE archive_entry ADD COLUMN iucn_status` (try/except for existing DBs).
- `app.py` — `entry_to_dict` now returns `"iucn": e.iucn_status or "LC"` — map pin colours now reflect real threat level.
- `app.py` — Silent `except: pass` on BirdNET replaced with `print(f"[WARNING] BirdNET analysis failed: {e}")`.
- `archive.html` — Entry cards now display: scientific name (italic mono), IUCN threat badge (colour-coded), BirdNET confidence percentage.
- `archive.html` — CSS added for `.entry-species-meta`, `.entry-sci-name`, `.entry-iucn-badge`, `.iucn-badge-{CR|EN|VU|NT|LC}`, `.entry-conf`.

## 2026-03-23

### Added
- `app.py` — `FolkloreEntry` model: stores title, body, location, lat/lng, user FK, timestamp, SHA-256 hash chain fields, and relationship to witnesses.
- `app.py` — `FolkloreWitness` model: stores entry FK, user FK, Algorand/SHA-256 token, timestamp. Unique constraint on `(entry_id, user_id)`.
- `app.py` — `folklore_to_dict()` helper returning id, title, location, lat/lng, timestamp, witness count.
- `app.py` — `/folklore/submit` (POST, login required): accepts title, body, optional `.txt` file upload, location; geocodes; builds hash chain; saves `FolkloreEntry`.
- `app.py` — `/folklore/witness/<id>` (POST): creates `FolkloreWitness` with Algorand notarisation (SHA-256 fallback); returns token + ledger ID as JSON.
- `app.py` — `/api/folklore-pins` (GET): returns up to 200 geotagged folklore entries as JSON for map rendering.
- `app.py` — `mapping_dissonance()` route updated to query `FolkloreEntry` and `FolkloreWitness` counts; passes `folklore_entries`, `folklore_json`, `witness_counts` to template.
- `app.py` — Startup purge: on boot, queries `ArchiveEntry` rows where `species_common` is `"Unknown"` or `None`, deletes their audio files from disk, removes the DB rows, and evicts matching `_news_cache` keys.
- `app.py` — BirdNET rejection gate in `upload()`: if both analysis passes return `common == "Unknown"`, the uploaded file is deleted with `os.remove()` and the request redirects without writing to the DB.
- `templates/archive.html` — Two-tab layout: "Sound Archive" and "Story Ledger" tabs within the same split-screen page. Tab state persists to URL hash (`#sounds` / `#stories`).
- `templates/archive.html` — Stories tab: intro text, submission form (textarea + `.txt` file upload + location field), paginated story feed with expand/collapse, witness ledger with inline receipt (token + ledger ID).
- `templates/archive.html` — Story pins on the archive map: outlined `book-open` Lucide icon (`.story-pin` CSS), visually distinct from sound pins. Clicking a story pin switches to the Stories tab and scrolls to the card.
- `templates/archive.html` — Nearby cross-reference boxes (5km Haversine radius): each sound entry card shows linked stories from the same region; each folklore card shows linked sound recordings from the same region. Boxes hidden when nothing is nearby.
- `templates/login.html` — Intro paragraph added: *"To return to the archive as a watcher, listener, and caretaker — sign in below."*
- `templates/register.html` — Intro paragraph added: *"Become a watcher, listener, and caretaker. Your recordings, stories, and signatures build the living memory of this landscape."*
- `static/style.css` — `.auth-intro` rule: body-size Helvetica, 0.7 opacity, golden-ratio line height.

### Changed
- `render.yaml` — `--workers 4` → `--workers 1`. Eliminates in-memory `_news_cache` split across processes; all requests share a single cache.
- `templates/landing.html` — Map `style.load` handler now calls `applyLineDrawnStyle()`: hides all road, street, transit, and rail symbol layers; suppresses small place labels (village, neighbourhood, suburb level); retains city, capital, country, and state labels only.
- `templates/bird_list.html` — Table wrapped in `.birdlist-table-wrap` with `max-height: calc(100vh - 360px)` and `overflow-y: auto`; `thead` is sticky so column headers stay visible while scrolling.
- `templates/bird_list.html` — IUCN legend items are now interactive filters: click any status badge to show only that threat level; click again to clear. Inactive items dim to 35% opacity when a filter is active. Filter state is independent of the text search (both apply simultaneously).
- `templates/bird_list.html` — Search bar and legend made `position: sticky` so they remain visible above the scrolling table.

## 2026-03-23 — Map Constraints + Location Picker

### Changed
- `templates/archive.html` — Both submission forms (field recording + story) now have a **"Pick from map →"** button under the Location input. Clicking it switches the archive map cursor to crosshair; a single click on the map reverse-geocodes via Nominatim and fills the field in `region, state, country` format. A fixed hint banner confirms pick mode is active.
- `templates/archive.html` — Archive map now has `minZoom: 5` and `maxBounds: [[71.0, 22.5], [83.5, 35.0]]` — a ~600 km bounding box centred on Delhi (77.209°E, 28.614°N). Users cannot pan or zoom outside this region.

## 2026-03-23 — Design Revamp Pass

### Changed
- `templates/landing.html`, `templates/archive.html`, `templates/bird_list.html` — Removed sketchy organic `border-radius` (`2px 255px 3px 25px / 255px 5px 225px 3px`). All `.sketchy-box` elements now use `border-radius: 0` (clean thin square borders).
- `templates/landing.html` — `applyLineDrawnStyle()` now hides ALL symbol layers (complete label removal). Previously kept city/capital labels; now purely pin-based navigation — no map text labels of any kind.
- `templates/landing.html` — Community Stories pins added to main map. Fetches `/api/folklore-pins` after sound archive pins load; plots with `book-open` Lucide icon in `#8a8a8a`. Stories now appear on the main page map for the first time.
- `templates/landing.html` — Map legend updated: added "Community Stories" category with `book-open` icon. `book-open` SVG path added to `LUCIDE_SVG`; `story` key added to `LAYER_ICON`.
- `templates/archive.html` — `hideAllLabels()` added; called on `style.load` and theme-change MutationObserver. Archive map now label-free, matching landing page.
- `templates/archive.html` — Map key overlay added (`.arc-map-key`, bottom-left absolute position): IUCN colour spectrum (black → grey) plus Community Story indicator (outline only).
- `templates/about.html` — Acknowledgements tab and panel added (data-idx="5"). Panel includes: Residency/Akademie Solitude gratitude, Inspirations placeholder, Field Contributors placeholder, Data & Tools credit. Progress dots LABELS array updated to include "Acknowledgements".
- `templates/bibliography.html` — Technology section expanded: added MapLibre GL (map rendering), NewsAPI (news aggregation), Algorand Foundation / Python SDK (blockchain), Flask + SQLAlchemy + Gunicorn (web stack).