# Runbook

This document outlines the operational procedures and health checks for Mapping Dissonance.

## Deployment Procedures

### Dokploy (Containerised Deployment)
1. Commit changes to the `main` branch.
2. Dokploy will trigger a rebuild based on the `Dockerfile`.
3. The application binds to port 80 using Gunicorn with 1 worker.
4. Persistent data is stored in Docker volumes:
   - `mapping-dissonance-db` -> `/app/instance`
   - `mapping-dissonance-uploads` -> `/app/static/uploads`

## Health Checks & Monitoring
- **Health Endpoint**: `GET /health` (returns JSON status).
- **Error Tracking**: Sentry is integrated (if `SENTRY_DSN` is set).
- **Logging**: Standard out/err logs from the Docker container.

## Common Issues & Fixes

### 429 Too Many Requests
- **Cause**: Flask-Limiter is hitting a limit, possibly due to a "Proxy-IP" trap.
- **Fix**: Ensure `ProxyFix` is correctly configured in `app.py` (`x_for=2` is currently set) and the `X-Forwarded-For` header is being sent by the reverse proxy.

### 404 Assets Not Loading
- **Cause**: Application hosted on a subpath (`/mapping-dissonance/`) but requesting assets from root.
- **Fix**: Use `url_for()` in all templates and ensure `ProxyFix` is configured with `x_prefix=1`.

### SQL Integrity Error on Startup
- **Cause**: Purging entries without associated cascade deletes.
- **Fix**: Ensure `ArchiveEntry` relationships have `cascade="all, delete-orphan"`.

## Rollback Procedures
1. Revert the last commit on the `main` branch.
2. Dokploy will rebuild the previous state.
3. If database schema was changed, a manual rollback of the persistent SQLite file may be required from backups.

## Alerting
Currently, Sentry notifies of any unhandled exceptions in production.
