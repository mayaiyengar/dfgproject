# DfG Policy Monitor

Internal policy-monitoring tool for Days for Girls Global Advocacy staff —
tracks new U.S. federal and state policy relevant to menstrual health and
menstrual equity.

**Planning documents** (read these first): [`docs/`](docs/) — architecture,
database schema, source inventory, risks/limitations, and roadmap. This
project follows those docs; if code and docs disagree, that's a bug in one
of them, not a judgment call to make silently.

## Status

Phase 1 in progress: Federal + California + Washington State ingestion
(`docs/roadmap.md` §1). No database, classification, or dashboard yet —
adapters currently normalize records for inspection, not persistence.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running tests

```bash
pytest
```

Tests never depend on live government websites or live third-party APIs —
everything runs against fixtures in `tests/fixtures/` and mocked HTTP
(`respx`). See `docs/architecture.md` §10.

## Configuration

Copy `.env.example` to `.env` and fill in what you have. Every value is
optional except where a specific adapter's docstring says otherwise — the
pipeline runs without `ANTHROPIC_API_KEY` by design (`docs/architecture.md`
design goal #2).

## Repository layout

See `docs/roadmap.md` §2 for the full structure and rationale.
