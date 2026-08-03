#!/bin/sh
set -e

# The in-config `exec:` field that litestream.yml uses to spawn uvicorn does
# NOT include the auto-restore-if-missing behavior that `litestream replicate
# -exec ...` (the CLI flag form) has -- that turned out the hard way, during
# a real production incident, to leave a brand new (empty) local database on
# every cold start instead of restoring the replicated one. So the restore is
# done explicitly here, before replication starts.
#
# -if-db-not-exists: no-op successfully if a local file is somehow already
#   present (defensive; Cloud Run's ephemeral disk means this is normally
#   never the case at cold start, but a same-instance process restart could
#   hit it).
# -if-replica-exists: no-op successfully instead of failing when there is no
#   backup yet (a brand new deployment with an empty bucket).
litestream restore -if-db-not-exists -if-replica-exists -config /etc/litestream.yml /app/db/todos.db

exec litestream replicate -config /etc/litestream.yml
