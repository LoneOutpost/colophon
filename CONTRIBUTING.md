# Contributing to Colophon

Thanks for your interest in improving Colophon. Bug reports, feature ideas, and
pull requests are all welcome. This guide covers how to get set up and the
conventions the project follows.

By participating you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

Colophon uses [uv](https://docs.astral.sh/uv/) for dependency management.

```
uv sync
```

You also need `ffmpeg` and `ffprobe` on your PATH for the encoding and
verification paths. Run the app locally with:

```
uv run python -m colophon
```

The interface is served at http://localhost:8080.

## Tests and linting

Both must pass before a change is merged. Run them locally before opening a pull
request:

```
uv run pytest
uv run ruff check .
```

The same checks run in CI on every push and pull request.

## Coding conventions

- The codebase follows a ports and adapters layout. `core` holds domain logic,
  `adapters` holds I/O, `services` holds the pipeline steps, `controller.py`
  orchestrates without touching the UI, and `ui` holds the NiceGUI pages. Keep
  new code on the correct side of those boundaries.
- Prefer editing an existing module over adding a new one when the responsibility
  already has a home. When a file grows too large to hold in your head, that is a
  signal to split it by responsibility.
- Use the logging module rather than `print`. Catch specific exceptions, not a
  bare `except`. Do not leave commented-out code or stray `TODO` markers behind.
- Add or update tests alongside behavior changes. Tests live under `tests/` and
  run with pytest.

## Pull requests

- Branch from `main` using a short prefixed name, for example `feature/...`,
  `fix/...`, or `chore/...`.
- Keep each pull request focused on one change. Smaller, self-contained changes
  are easier to review and revert.
- Write a clear description of what changed and why, and note how you verified it.
- Make sure `uv run pytest` and `uv run ruff check .` are green.

## Reporting bugs and requesting features

Open an issue using the templates in the issue tracker. For bugs, include your
operating system, Python version, the steps to reproduce, and what you expected
to happen. For security issues, follow [SECURITY.md](SECURITY.md) instead of
opening a public issue.
