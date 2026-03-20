# Mapping Dissonance

A web application for archiving field recordings of bird species in Delhi, building a tamper-evident ledger of ecological presence and community acknowledgment.

## What it does

- Users upload audio recordings tagged with a location, species (via Merlin), date, and time of day
- Each entry is chained via SHA-256 hashes to form a verifiable archive
- Logged-in users can sign a "care signature" on any entry — an on-chain notarisation on Algorand Testnet (falls back to a local SHA-256 token if no wallet is configured)
- A map on the landing page visualises recording locations, clustered and colour-coded by IUCN conservation status
- Context news for each location is fetched from curated Indian environmental RSS feeds, GDELT, or NewsAPI (in that order of preference)

## Stack

- Python / Flask
- SQLAlchemy + SQLite (swappable via `DATABASE_URL`)
- Flask-Login for authentication
- Gunicorn as the production WSGI server
- BirdNET Analyzer for automatic species detection (optional — requires TensorFlow, disabled by default)
- Algorand SDK for on-chain notarisation (optional)

## Local development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Runs on `http://127.0.0.1:5001`.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes (production) | Flask session secret |
| `NEWS_API_KEY` | No | NewsAPI key (Tier 3 news fallback) |
| `IUCN_TOKEN` | No | IUCN Red List API bearer token |
| `ALGORAND_MNEMONIC` | No | 25-word mnemonic for Algorand Testnet notarisation |
| `DATABASE_URL` | No | SQLAlchemy URI — defaults to SQLite in `instance/` |

Copy `.env.example` to `.env` for local development (not committed).

## Deployment

See server deployment notes for Dokploy / vserv09 configuration. The short version:

1. Build via the `Dockerfile` (gunicorn binds to port 80)
2. Mount `/app/instance` and `/app/static/uploads` as persistent volumes
3. Set `SECRET_KEY` in the environment
4. Map the domain in the Dokploy Domains UI — no manual Traefik labels
