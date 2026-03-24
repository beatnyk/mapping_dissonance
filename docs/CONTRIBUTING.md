# Contributing Guide

Welcome to Mapping Dissonance. This guide will help you set up your development environment and understand how to contribute.

## Development Environment Setup

### Prerequisites
- Python 3.12+
- `venv` for environment isolation

### Installation
1. Clone the repository.
2. Create and activate a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run the post-install shim for BirdNET:
   ```bash
   python setup_birdnet.py
   ```
5. Set up your environment:
   ```bash
   cp .env.example .env
   ```

## Available Scripts

<!-- AUTO-GENERATED:COMMANDS -->
| Command | Description |
|---------|-------------|
| `python app.py` | Start the local development server |
| `gunicorn app:app` | Start the production server (WSGI) |
| `python setup_birdnet.py` | Configure the BirdNET shim for Python 3.12+ |
<!-- /AUTO-GENERATED:COMMANDS -->

## Testing Procedures
The project currently uses manual validation. When adding features, please ensure you:
1. Verify the changes locally on `http://127.0.0.1:5001`.
2. Check that database migrations (if any) are handled.
3. Verify that the UI remains consistent across both light and dark themes.

## Code Style
- Follow PEP 8 for Python code.
- Use semantic HTML and Vanilla CSS.
- Ensure all new features are compatible with the reverse-proxy subpath configuration.

## PR Submission Checklist
- [ ] Code is documented and follows PEP 8.
- [ ] Environment variables are added to `.env.example` if necessary.
- [ ] No hardcoded absolute paths (use `url_for`).
- [ ] Subpath compatibility is verified.
