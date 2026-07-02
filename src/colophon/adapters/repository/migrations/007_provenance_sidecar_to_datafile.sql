-- Heal the SIDECAR -> DATAFILE provenance rename (code commit 7ed06c6) that never migrated
-- persisted rows. Book records written before that rename store the provenance value "sidecar",
-- which current code neither produces nor recognizes: drop_orphaned_datafile_fields matches
-- "datafile", so a removed sidecar's field is never cleared and the stale value is re-adopted on
-- every scan (immortal poison, e.g. an upload-folder name stuck as the author). Rewrite the legacy
-- value to "datafile" on exactly the provenance keys a datafile sidecar can fill, so the existing
-- orphan-cleanup heals them again on the next scan. Only provenance values are touched (never a
-- title/name that merely reads "sidecar"), and each statement is a no-op when the key is absent or
-- already "datafile", so re-running is safe.
UPDATE book_units SET data = json_replace(data, '$.provenance.title', 'datafile')
  WHERE json_extract(data, '$.provenance.title') = 'sidecar';
UPDATE book_units SET data = json_replace(data, '$.provenance.subtitle', 'datafile')
  WHERE json_extract(data, '$.provenance.subtitle') = 'sidecar';
UPDATE book_units SET data = json_replace(data, '$.provenance.authors', 'datafile')
  WHERE json_extract(data, '$.provenance.authors') = 'sidecar';
UPDATE book_units SET data = json_replace(data, '$.provenance.narrators', 'datafile')
  WHERE json_extract(data, '$.provenance.narrators') = 'sidecar';
UPDATE book_units SET data = json_replace(data, '$.provenance.series', 'datafile')
  WHERE json_extract(data, '$.provenance.series') = 'sidecar';
UPDATE book_units SET data = json_replace(data, '$.provenance.publish_year', 'datafile')
  WHERE json_extract(data, '$.provenance.publish_year') = 'sidecar';
UPDATE book_units SET data = json_replace(data, '$.provenance.publisher', 'datafile')
  WHERE json_extract(data, '$.provenance.publisher') = 'sidecar';
UPDATE book_units SET data = json_replace(data, '$.provenance.description', 'datafile')
  WHERE json_extract(data, '$.provenance.description') = 'sidecar';
UPDATE book_units SET data = json_replace(data, '$.provenance.asin', 'datafile')
  WHERE json_extract(data, '$.provenance.asin') = 'sidecar';
UPDATE book_units SET data = json_replace(data, '$.provenance.isbn', 'datafile')
  WHERE json_extract(data, '$.provenance.isbn') = 'sidecar';
