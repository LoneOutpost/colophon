# Colophon

Colophon is a self-hosted tool for organizing a personal audiobook collection.
It scans existing audiobook files, identifies them using their embedded tags and
external metadata sources, lets you review and correct the metadata, encodes each
book into a single chaptered M4B, and files it on disk using your LazyLibrarian
folder layout.

It is a single-user tool meant to run on a trusted local network. It is not
hardened for public internet exposure.

## Requirements

* Python 3.12 or newer
* [uv](https://docs.astral.sh/uv/)
* ffmpeg and ffprobe on your PATH (used for M4B encoding and verification)

## Install and run

```
uv sync
uv run python -m colophon
```

The web interface is served at http://localhost:8080 with three pages:
dashboard (`/`), triage (`/triage`), and settings (`/settings`).

## Configuration

Open the settings page and fill in the values below, or edit the TOML file
directly. It lives in your XDG config directory, for example
`~/.config/colophon/config.toml`.

* Scan paths: folders to ingest, one per line. Each folder that directly
  contains audio files is treated as one book.
* Library root: where organized M4B files are written.
* LazyLibrarian config.ini path: Colophon reads the audiobook folder and file
  patterns from this file so its output matches your LazyLibrarian layout.
* Filename template: a pattern such as `%author% - %title%` used to parse
  filenames when tags are missing.
* Review threshold: the confidence score (0 to 100) at or above which a book is
  marked ready automatically. Default is 75.
* Transcode bitrate: AAC bitrate for MP3-to-M4B transcoding. Default is 64k.
* AudiobookShelf URL, token, and library id: used to trigger a library rescan
  after books are organized.
* LazyLibrarian URL and API key: used for read-only status lookups.
* Hardcover API token: enables the Hardcover metadata source.

Changing the database path requires a restart.

## How it works

1. Scan. Colophon discovers books, reads embedded tags, any `metadata.json`
   sidecar next to the files, and hints from the folder and file names. It
   reconciles these into one candidate record per book and records where each
   field came from. Embedded tags take precedence, then the sidecar, then the
   folder name, then the filename.
2. Identify. It queries the configured metadata sources, scores a confidence
   value for each book, and routes each one to ready or needs review.
3. Triage. Books are grouped by author and series, with a separate group for
   books that could not be identified. For each book you can view the field
   provenance, compare candidate matches and apply one, edit or remap fields, and
   mark the book ready. Every change is written back to the source
   `metadata.json` and can be undone.
4. Encode and organize. Ready books are combined into a single verified M4B with
   chapters, moved into the LazyLibrarian-derived path, and given a corrected
   `metadata.json`. If AudiobookShelf is configured, a rescan is triggered.
   Original files are deleted only after a verified encode and an explicit
   confirmation.

## Metadata sources

* Audnexus: audiobook-specific data including narrator, series, ASIN, and cover.
  A matching ASIN yields high confidence.
* OpenLibrary: title and author fallback.
* Google Books: broad catalog fallback, no authentication required.
* Hardcover: enabled when an API token is configured.

## Development

```
uv run pytest
uv run ruff check .
```

The code follows a ports and adapters layout: `core` holds the domain logic,
`adapters` holds I/O (audio tags, ffmpeg, the SQLite store, HTTP clients, and
sidecar reading and writing), `services` holds the pipeline steps, `controller.py`
provides UI-agnostic orchestration, and `ui` holds the NiceGUI pages.

## License

Apache License 2.0. See [LICENSE](LICENSE).
