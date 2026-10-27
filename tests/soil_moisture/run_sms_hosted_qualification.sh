#!/usr/bin/env bash
set -euo pipefail
# Called after provisioning on the disposable runner; only namespace setup is privileged.
root="${RUNNER_TEMP:?}/sms-hosted-qa"
source_root="${GITHUB_WORKSPACE:?}"
runner_uid="$(id -u)"
runner_gid="$(id -g)"
outer_namespace="$(readlink /proc/self/ns/net)"
mkdir -p "${root}/private-home" "${root}/tmp"
free_kib="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"
[ "${free_kib}" -gt 5242880 ] || { echo 'Insufficient safe runner memory'; exit 1; }
# The namespace starts with loopback only. No veth, resolver or external route is installed.
sudo unshare --net --fork bash -c '
  set -euo pipefail
  ip link set lo up
  exec setpriv --reuid="$1" --regid="$2" --init-groups --no-new-privs \
    env -i PATH="$3" HOME="$4/private-home" TMPDIR="$4/tmp" \
    LANG=C.UTF-8 TZ=UTC PYTHONDONTWRITEBYTECODE=1 \
    GIT_OPTIONAL_LOCKS=0 GIT_NO_LAZY_FETCH=1 \
    R_LIBS_USER="$5" R_LIBS_SITE="$6" R_HOME="$7" LD_LIBRARY_PATH="$8" \
    R_ENVIRON_USER=/dev/null R_PROFILE_USER=/dev/null R_MAKEVARS_USER=/dev/null \
    GITHUB_SHA="$9" GITHUB_RUN_ID="${10}" GITHUB_RUN_ATTEMPT="${11}" \
    GITHUB_WORKSPACE="${12}" SMS_OUTER_NETNS="${13}" \
    python3 -B "${12}/tests/soil_moisture/run_sms_hosted_qualification.py" supervise \
      --output "$4/test-monitor" --seconds 660 --trigger 3221225472 --ceiling 4294967296 -- \
      python3 -B "${12}/tests/soil_moisture/run_sms_hosted_qualification.py" qualify --output "$4/tests"
' namespace-setup "${runner_uid}" "${runner_gid}" "${PATH}" "${root}" \
  "${R_LIBS_USER:?}" "${R_LIBS_SITE:-}" "${R_HOME:-}" "${LD_LIBRARY_PATH:-}" \
  "${GITHUB_SHA}" "${GITHUB_RUN_ID}" "${GITHUB_RUN_ATTEMPT}" "${source_root}" "${outer_namespace}"
