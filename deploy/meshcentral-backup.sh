#!/bin/sh
# fleet meshcentral backup - conservative 7 day rolling tar of server state.
set -eu

BACKUP_DIR=/var/lib/fleet/meshcentral-backups
mkdir -p "$BACKUP_DIR"
STAMP=$(date +%Y%m%d%H%M)
tar -C /opt/meshcentral -czf "$BACKUP_DIR/meshcentral-${STAMP}.tar.gz" meshcentral-data meshcentral-files
find "$BACKUP_DIR" -type f -name 'meshcentral-*.tar.gz' -mtime +7 -delete
chmod 600 "$BACKUP_DIR"/meshcentral-*.tar.gz