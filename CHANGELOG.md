# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-11

First tagged release.

### Added

- Scanning of local audiobook folders, reading embedded tags, a `metadata.json`
  sidecar when present, and hints from folder and file names, reconciled into one
  candidate record per book with per-field provenance.
- Identification against Audnexus, OpenLibrary, Google Books, and Internet
  Archive, plus any providers exposed by a configured abs-agg instance, with a
  confidence score that routes each book to ready or needs review.
- A web interface with Library, Manage, Stats, Graph, and Settings views, and a
  contextual Acquire view. Books can be reviewed by author and series, compared
  against candidate matches, edited or remapped field by field, and marked ready.
  Every change is written back to the source `metadata.json` and can be undone.
- Encoding of ready books into a single verified, chaptered M4B, filed into a
  LazyLibrarian-derived path. Original files are deleted only after a verified
  encode and an explicit confirmation.
- An optional AudiobookShelf rescan triggered after organizing, and read-only
  LazyLibrarian status lookups.
- First-run configuration written to the XDG config directory, editable from the
  Settings page, covering scan paths, the library root, naming patterns, the
  review threshold, the transcode bitrate, and integration credentials.

[Unreleased]: https://github.com/LoneOutpost/colophon/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/LoneOutpost/colophon/releases/tag/v0.1.0
