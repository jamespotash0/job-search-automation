#!/usr/bin/env bash
# Decide whether the schedule that fired is the one currently in effect for ET.
#
# GitHub Actions cron is UTC-only — there is no timezone option — so a schedule
# pinned to summer ET silently slides an hour earlier when the clocks fall back.
# The fix: register BOTH the EDT and the EST cron for every slot, and let this
# gate drop whichever set is not in effect right now.
#
# Gating is on the cron expression that FIRED (github.event.schedule), never on
# the wall clock. Actions has no SLA for scheduled runs and routinely starts them
# 60-90 min late, so a "is it 9am yet?" check would see the wrong hour and skip a
# run that was in fact scheduled correctly.
#
# The UTC->ET mapping is derived, not duplicated: we pull the first UTC hour out
# of the cron that fired, shift it by ET's current offset, and run only if it
# lands on one of TARGET_ET_HOURS. Editing the crons in the workflow YAML needs
# no matching edit here. The tradeoff is that a cron whose hours map to no target
# hour never runs, so keep the two in sync — the log line below says what it saw.
#
# Ambiguity is resolved toward running: an unparseable offset or cron still fires
# the digest. A digest at an odd hour beats a digest that silently stopped.
#
# Inputs:  TARGET_ET_HOURS  space-separated ET hours, 24h clock (e.g. "8 12 15")
#          SCHEDULE         the cron that fired; empty for non-schedule triggers
# Output:  run=true|false   appended to $GITHUB_OUTPUT
set -uo pipefail

TARGET_ET_HOURS="${TARGET_ET_HOURS:?set TARGET_ET_HOURS to the ET hours this workflow should run at}"
SCHEDULE="${SCHEDULE-}"

emit() { echo "run=$1" >> "${GITHUB_OUTPUT:-/dev/stdout}"; }

# workflow_dispatch and everything else that isn't cron: always run.
if [ -z "$SCHEDULE" ]; then
  echo "not a schedule trigger; running"
  emit true; exit 0
fi

# ET's UTC offset right now: -0400 under EDT, -0500 under EST.
offset="$(TZ=America/New_York date +%z)"
case "$offset" in
  [+-][0-9][0-9][0-9][0-9]) ;;
  *) echo "[warn] unexpected offset '$offset'; running rather than skipping"; emit true; exit 0 ;;
esac
delta=$(( 10#${offset:1:2} ))
[ "${offset:0:1}" = "-" ] && delta=$(( -delta ))

# First UTC hour of the cron that fired: "57 12,16,19 * * *" -> 12.
utc_hour="$(echo "$SCHEDULE" | awk '{print $2}' | cut -d, -f1)"
case "$utc_hour" in
  ''|*[!0-9]*) echo "[warn] cannot parse hour from cron '$SCHEDULE'; running"; emit true; exit 0 ;;
esac
et_hour=$(( ( 10#$utc_hour + delta + 24 ) % 24 ))

for h in $TARGET_ET_HOURS; do
  if [ "$et_hour" -eq "$(( 10#$h ))" ]; then
    echo "cron '$SCHEDULE' maps to ${et_hour}:xx ET (offset $offset) — in effect, running"
    emit true; exit 0
  fi
done

echo "cron '$SCHEDULE' maps to ${et_hour}:xx ET (offset $offset) — not a target hour ($TARGET_ET_HOURS), skipping"
emit false
