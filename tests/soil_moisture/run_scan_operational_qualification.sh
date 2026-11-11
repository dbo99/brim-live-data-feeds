#!/usr/bin/env bash
set -euo pipefail
root="${RUNNER_TEMP:?}/scan-qualification"
mkdir -p "$root/home" "$root/tmp"
outer="$(readlink /proc/self/ns/net)"
test "$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)" -gt 5242880
sudo unshare --net --fork bash -c '
  set -euo pipefail
  ip link set lo up
  exec setpriv --reuid="$1" --regid="$2" --init-groups --no-new-privs \
    env -i PATH="$3" HOME="$4/home" TMPDIR="$4/tmp" LANG=C.UTF-8 TZ=UTC \
    PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0 GIT_NO_LAZY_FETCH=1 \
    R_LIBS_USER="$5" R_LIBS_SITE="$6" R_HOME="$7" LD_LIBRARY_PATH="$8" \
    R_ENVIRON_USER=/dev/null R_PROFILE_USER=/dev/null R_MAKEVARS_USER=/dev/null \
    GITHUB_SHA="$9" SMS_OUTER_NETNS="${10}" \
    python3 -B "${11}/tests/soil_moisture/run_sms_hosted_qualification.py" supervise \
      --output "$4/monitor" --seconds 270 -- \
      python3 -B "${11}/tests/soil_moisture/run_scan_operational_qualification.py" --output "$4/tests"
' namespace-setup "$(id -u)" "$(id -g)" "$PATH" "$root" \
  "${R_LIBS_USER:?}" "${R_LIBS_SITE:-}" "${R_HOME:-}" "${LD_LIBRARY_PATH:-}" \
  "$GITHUB_SHA" "$outer" "$GITHUB_WORKSPACE"
cp "$root/tests/hosted-qualification.json" "${RUNNER_TEMP}/scan-control/hosted-qualification.json"
