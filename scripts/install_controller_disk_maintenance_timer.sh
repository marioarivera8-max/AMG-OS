#!/usr/bin/env bash
set -euo pipefail

# Install an opt-in host-side disk maintenance timer for the controller VM.
# This must run on the Hetzner host as root. It does not run cleanup during
# installation unless --run-now is passed.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEEP_DOCKER_TAGS="${KEEP_DOCKER_TAGS:-2}"
RUN_NOW=0

for arg in "$@"; do
  case "$arg" in
    --run-now)
      RUN_NOW=1
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root on the controller host." >&2
  exit 1
fi

install -d /etc/systemd/system

cat >/etc/systemd/system/amg-disk-maintenance.service <<EOF
[Unit]
Description=AMG disk maintenance
Documentation=file://${REPO_ROOT}/docs/cloud_edition_runbook.md

[Service]
Type=oneshot
ExecStart=/usr/bin/env python3 ${REPO_ROOT}/scripts/controller_disk_maintenance.py --apply --keep-docker-tags ${KEEP_DOCKER_TAGS}
EOF

cat >/etc/systemd/system/amg-disk-maintenance.timer <<'EOF'
[Unit]
Description=Run AMG disk maintenance daily

[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=30m

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now amg-disk-maintenance.timer

if [ "$RUN_NOW" -eq 1 ]; then
  systemctl start amg-disk-maintenance.service
fi

systemctl list-timers amg-disk-maintenance.timer --no-pager
