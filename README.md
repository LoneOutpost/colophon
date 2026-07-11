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

## Run with Docker

The published image bundles ffmpeg, so Docker is the simplest way to run
Colophon. It is multi-arch (amd64 and arm64), so it runs on an x86 server or an
arm64 NAS or Raspberry Pi.

```
docker pull ghcr.io/loneoutpost/colophon:latest
```

Copy [`docker-compose.yml`](docker-compose.yml), edit the two media paths, and
start it:

```
docker compose up -d
```

Colophon listens on port 8080. Set `PUID` and `PGID` to your media user so
organized files land with the right ownership.

| Volume | Purpose | Mode |
|---|---|---|
| `/config` | `config.toml` and the SQLite database | read-write |
| `/downloads` | where Acquire downloads land; also add it as a scan path | read-write |
| `/library` | the organize destination AudiobookShelf reads | read-write |
| `/media` | optional: an existing collection to import, as extra scan paths | read-write |
| `/lazylibrarian/config.ini` | optional: LazyLibrarian config for the pattern import | read-only |

The input volumes must be read-write. Colophon writes a `metadata.json` sidecar
back next to the source files and, after a verified encode and an explicit
confirmation, deletes the originals.

On first start Colophon writes a default `config.toml` to `/config`. Set
`scan_paths` to `["/downloads"]`, `library_root` to `/library`, and the Real-Debrid
download directory to `/downloads`, either by editing that file or from the
Settings page, then restart. Colophon will offer to add the downloads directory
to your scan paths.

## Install and run

```
uv sync
uv run python -m colophon
```

The web interface is served at http://localhost:8080 with three pages:
dashboard (`/`), triage (`/triage`), and settings (`/settings`).

## Configuration

On first run Colophon writes a commented `config.toml` with default values to
your XDG config directory, for example `~/.config/colophon/config.toml`. Edit it
and restart, or change values from the Settings page in the web interface. The
table below lists every setting.

| Setting | Default | Required | Purpose |
|---|---|---|---|
| `scan_paths` | empty list | yes, to scan | Folders to ingest. Each folder that directly contains audio files is one book. |
| `library_root` | unset | yes, to organize | Destination root for organized M4B files. |
| `lazylibrarian_config_ini` | unset | no | Path to LazyLibrarian's config.ini. Its audiobook folder and file patterns are read so output matches your LazyLibrarian layout. |
| `filename_template` | `$Author - $Title` | no | `$Token` pattern for extracting metadata from filenames when embedded tags are missing. |
| `directory_scheme` | empty | no | `$Token` pattern for inferring fields from the folder hierarchy, e.g. `$Author/$Series/$Title`. Blank disables it. |
| `organize_folder_pattern` | `$Author/$Title` | no | `$Token` pattern for the folder layout of organized M4Bs. Wrap optional text in `[ ... ]` to render it only when its token has a value; a group drops if any token inside is empty; use `[[` and `]]` for literal brackets. |
| `organize_file_pattern` | `$Title` | no | `$Token` pattern for the organized file name (no extension). Supports the same `[ ... ]` conditional groups, e.g. `[$SerNum - ]$Title`. |
| `review_threshold` | `75.0` | no | Confidence (0 to 100) at or above which a book is marked ready automatically. |
| `transcode_bitrate` | `64k` | no | AAC bitrate used when transcoding MP3 sources into M4B. |
| `port` | `8080` | no | Port the web interface listens on. |
| `root_path` | empty | no | URL base path when served behind a reverse proxy, for example `/colophon`. Empty serves at the root path. |
| `db_path` | standard data dir | no | SQLite database location. Changing it requires a restart. |
| `worker_pool_size` | unset | no | Reserved for future concurrent encoding. Not used yet. |
| `audiobookshelf_url` | unset | no | AudiobookShelf base URL. With the token and library id, used to trigger a rescan after organizing. |
| `audiobookshelf_token` | unset | no | AudiobookShelf API token. |
| `audiobookshelf_library_id` | unset | no | AudiobookShelf library to rescan. |
| `lazylibrarian_url` | unset | no | LazyLibrarian base URL for read-only status lookups. |
| `lazylibrarian_api_key` | unset | no | LazyLibrarian API key. |
| `abs_agg_url` | unset | no | Base URL of a self-hosted [abs-agg](https://github.com/Vito0912/abs-agg) instance. When set, its providers are auto-discovered and added as metadata sources. |

Credentials are stored in this file. Keep it outside any shared or version
controlled location.

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
* OpenLibrary: title and author fallback, with ISBN.
* Google Books: broad catalog fallback, no authentication required.
* Internet Archive: spoken-word and LibriVox recordings.
* abs-agg: when `abs_agg_url` is set, each provider it aggregates (Hardcover,
  Goodreads, LibriVox, BookBeat, Storytel, and more) is auto-discovered and added
  as its own source.

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
