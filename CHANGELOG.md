# Changelog

## 2026-03-20

### Added
- `Dockerfile` — builds the app with `python:3.12-slim`, installs dependencies, exposes port 80, runs gunicorn. Port 80 is intentional for Dokploy/Traefik compatibility (same principle as sftp-manager).
- `.dockerignore` — excludes `venv/`, `__pycache__/`, `instance/`, `static/uploads/`, `.env`, `.git/` from the Docker build context.
- `README.md` — covers project overview, stack, local dev setup, environment variables, and deployment summary.

### Changed
- `app.py` — moved `db.create_all()` to module level (outside `if __name__ == '__main__'`) so the database tables are created on startup when running under gunicorn, not just when invoking the script directly.
