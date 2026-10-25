# Historical runtime provenance audit

F-02 repair audit procedure, prepared 2026-10-23. **NOT RUN against existing
sessions.** No existing database, demo, container or stored session was inspected
for this repair. Historical exposure remains unknown.

After separate authorization to inspect existing data, run the following read-only
query against an approved isolated restored copy. Keep the original backup and
pinned application image. Do not run current reducers against old sessions to
reinterpret results; code fingerprints may legitimately differ. Do not update,
relabel or delete historical evidence.

```sql
BEGIN TRANSACTION READ ONLY;
WITH observed_events AS (
    SELECT e.session_id, e.sequence, e.event_key,
           e.event->'payload'->>'action' AS action
    FROM runtime_events AS e
    JOIN runtime_sessions AS s USING (session_id)
    WHERE s.config->>'data_mode' = 'OBSERVED'
), source_events AS (
    SELECT *,
           lag(action) OVER w AS previous_action,
           lag(event_key) OVER w AS previous_key
    FROM observed_events
    WHERE action IN ('poll', 'tick', 'failure')
    WINDOW w AS (PARTITION BY session_id ORDER BY sequence)
)
SELECT session_id, sequence, event_key, previous_action, previous_key
FROM source_events
WHERE action = 'tick'
  AND (
      event_key !~ '^poll:[1-9][0-9]*:result$'
      OR previous_action IS DISTINCT FROM 'poll'
      OR previous_key IS NULL
      OR event_key IS DISTINCT FROM previous_key || ':result'
  )
ORDER BY session_id, sequence;
ROLLBACK;
```

The query checks tick keys and their preceding unresolved poll, ignoring intervening
operator controls. A returned row is a protocol anomaly requiring investigation;
exclude that session from observed evaluation claims pending review. Record the
restored backup identity, query version, timestamp, row count and affected session
IDs in the audit report. Keep raw source bodies and credentials out of that report.

Zero rows do **not** prove genuine network acquisition or historical worker-lease
ownership: old events did not attest those facts. Correctly formatted keys and
OBSERVED labels alone cannot establish them. Compare available acquisition logs
and archived raw provenance independently before using historical sessions as
observed evidence. Do not infer evidence from synthetic test fixtures.

The repair prevents ordinary application paths from creating new unleased
observed poll/tick/failure events, rejects observed CLI steps, requires matching
pending poll/result keys, and removes implicit OBSERVED capture defaults. Injected
HTTP clients can produce only FIXTURE captures; the owned public HTTP client is
the production observed acquisition path. This is an application boundary, not
protection against a database owner, modified Python code or forged historical
backups. Structural unit and integration tests use explicitly labeled synthetic
capture data to exercise validation; their results are never market observations.
