#!/usr/bin/env bash

# Prefer Canonical's Ubuntu archive while retaining GitHub's Azure mirror as a
# fallback. This intentionally understands only the current GitHub-hosted
# Ubuntu mirror-file shape; unfamiliar runner configuration is a successful
# no-op so a future runner-image change cannot break a live feed by itself.

set -uo pipefail

mirror_file="${BRIM_APT_MIRRORS_FILE:-/etc/apt/apt-mirrors.txt}"
os_release_file="${BRIM_APT_OS_RELEASE_FILE:-/etc/os-release}"
platform="${BRIM_APT_PLATFORM_OVERRIDE:-$(uname -s 2>/dev/null || printf 'unknown')}"
candidate=""
staged=""

warn_and_continue() {
  local message="$1"
  printf '::warning title=BRIM APT mirror hardening::%s\n' "${message}" >&2
  printf 'BRIM APT MIRROR HARDENING WARNING: %s\n' "${message}" >&2
}

cleanup() {
  if [[ -n "${candidate}" && -e "${candidate}" ]]; then
    rm -f -- "${candidate}"
  fi
  if [[ -n "${staged}" && -e "${staged}" ]]; then
    rm -f -- "${staged}"
  fi
}
trap cleanup EXIT

read_os_id() {
  local key=""
  local value=""

  while IFS='=' read -r key value || [[ -n "${key}${value}" ]]; do
    if [[ "${key}" == "ID" ]]; then
      value="${value#\"}"
      value="${value%\"}"
      value="${value#\'}"
      value="${value%\'}"
      printf '%s' "${value}"
      return 0
    fi
  done < "${os_release_file}"
}

log_configuration() {
  local label="$1"
  printf 'BRIM APT mirror hardening: %s (%s)\n' "${label}" "${mirror_file}"
  if ! cat -- "${mirror_file}"; then
    return 1
  fi
  printf '\n'
}

log_effective_order() {
  printf '%s\n' \
    'BRIM APT mirror hardening: effective preference is:' \
    '  1. https://archive.ubuntu.com/ubuntu/' \
    '  2. http://azure.archive.ubuntu.com/ubuntu/' \
    '  3. https://security.ubuntu.com/ubuntu/'
}

if [[ "${platform}" != "Linux" ]]; then
  warn_and_continue "Expected Linux but found ${platform}; leaving APT configuration untouched."
  exit 0
fi

if [[ ! -r "${os_release_file}" ]]; then
  warn_and_continue "Cannot read ${os_release_file}; leaving APT configuration untouched."
  exit 0
fi

os_id="$(read_os_id)"
if [[ "${os_id}" != "ubuntu" ]]; then
  warn_and_continue "Expected Ubuntu but found OS ID '${os_id:-unknown}'; leaving APT configuration untouched."
  exit 0
fi

if [[ "${mirror_file}" != /* ]]; then
  warn_and_continue "Mirror-file path must be absolute; leaving APT configuration untouched."
  exit 0
fi

if [[ ! -e "${mirror_file}" ]]; then
  warn_and_continue "${mirror_file} is missing; continuing with the runner's default APT behavior."
  exit 0
fi

if [[ ! -f "${mirror_file}" || -L "${mirror_file}" || ! -r "${mirror_file}" ]]; then
  warn_and_continue "${mirror_file} is not a readable regular non-symlink file; leaving it untouched."
  exit 0
fi

if ! log_configuration "existing configuration"; then
  warn_and_continue "Could not log ${mirror_file}; leaving it untouched."
  exit 0
fi

entry_pattern='^([[:space:]]*)(https?://)(azure\.archive\.ubuntu\.com|archive\.ubuntu\.com|security\.ubuntu\.com)(/ubuntu/?)([[:space:]]+)(priority:([0-9]+))([[:space:]]*(#.*)?)$'
transformed=()
line_index=0
active_count=0
unknown_active=0
canonical_count=0
azure_count=0
security_count=0
canonical_priority=""
azure_priority=""
security_priority=""

while IFS= read -r line || [[ -n "${line}" ]]; do
  transformed[${line_index}]="${line}"

  if [[ "${line}" =~ ^[[:space:]]*$ || "${line}" =~ ^[[:space:]]*# ]]; then
    :
  elif [[ "${line}" =~ ${entry_pattern} ]]; then
    leading="${BASH_REMATCH[1]}"
    host="${BASH_REMATCH[3]}"
    separator="${BASH_REMATCH[5]}"
    priority="${BASH_REMATCH[7]}"
    suffix="${BASH_REMATCH[8]}"
    active_count=$((active_count + 1))

    case "${host}" in
      archive.ubuntu.com)
        canonical_count=$((canonical_count + 1))
        canonical_priority="${priority}"
        transformed[${line_index}]="${leading}https://archive.ubuntu.com/ubuntu/${separator}priority:1${suffix}"
        ;;
      azure.archive.ubuntu.com)
        azure_count=$((azure_count + 1))
        azure_priority="${priority}"
        transformed[${line_index}]="${leading}http://azure.archive.ubuntu.com/ubuntu/${separator}priority:2${suffix}"
        ;;
      security.ubuntu.com)
        security_count=$((security_count + 1))
        security_priority="${priority}"
        transformed[${line_index}]="${leading}https://security.ubuntu.com/ubuntu/${separator}priority:3${suffix}"
        ;;
    esac
  else
    unknown_active=1
  fi

  line_index=$((line_index + 1))
done < "${mirror_file}"

if (( unknown_active != 0 || active_count != 3 ||
      canonical_count != 1 || azure_count != 1 || security_count != 1 )); then
  warn_and_continue "Mirror layout is not the expected three-official-endpoint shape; preserving the original file byte-for-byte."
  exit 0
fi

if [[ ! "${canonical_priority}" =~ ^[123]$ ||
      ! "${azure_priority}" =~ ^[123]$ ||
      ! "${security_priority}" =~ ^[123]$ ||
      "${canonical_priority}" == "${azure_priority}" ||
      "${canonical_priority}" == "${security_priority}" ||
      "${azure_priority}" == "${security_priority}" ]]; then
  warn_and_continue "Mirror priorities are not one unique use each of 1, 2, and 3; preserving the original file byte-for-byte."
  exit 0
fi

ends_with_newline=0
if [[ -s "${mirror_file}" ]]; then
  final_byte_line_count="$(tail -c 1 -- "${mirror_file}" 2>/dev/null | wc -l | tr -d '[:space:]')"
  if [[ "${final_byte_line_count}" == "1" ]]; then
    ends_with_newline=1
  fi
fi

candidate="$(mktemp "${TMPDIR:-/tmp}/brim-apt-mirrors.XXXXXX")"
if [[ -z "${candidate}" ]]; then
  warn_and_continue "Could not allocate a candidate file; leaving ${mirror_file} untouched."
  exit 0
fi

write_failed=0
for ((index = 0; index < line_index; index++)); do
  if (( index + 1 < line_index || ends_with_newline == 1 )); then
    if ! printf '%s\n' "${transformed[${index}]}" >> "${candidate}"; then
      write_failed=1
      break
    fi
  elif ! printf '%s' "${transformed[${index}]}" >> "${candidate}"; then
    write_failed=1
    break
  fi
done

if (( write_failed != 0 )); then
  warn_and_continue "Could not build the validated candidate file; leaving ${mirror_file} untouched."
  exit 0
fi

if cmp -s -- "${candidate}" "${mirror_file}"; then
  printf '%s\n' 'BRIM APT mirror hardening: Canonical-first configuration is already active; no rewrite needed.'
  log_effective_order
  exit 0
fi

mirror_dir="$(dirname -- "${mirror_file}")"
if [[ ! -w "${mirror_file}" || ! -w "${mirror_dir}" ]]; then
  warn_and_continue "Insufficient permission for an atomic replacement of ${mirror_file}; leaving it untouched."
  exit 0
fi

staged="$(mktemp "${mirror_file}.brim.XXXXXX")"
if [[ -z "${staged}" ]]; then
  warn_and_continue "Could not allocate an atomic replacement beside ${mirror_file}; leaving it untouched."
  exit 0
fi

if ! cp -p -- "${mirror_file}" "${staged}" ||
   ! cp -- "${candidate}" "${staged}" ||
   ! mv -f -- "${staged}" "${mirror_file}"; then
  warn_and_continue "Atomic mirror-file replacement failed; the original path was not intentionally rewritten."
  exit 0
fi
staged=""

if ! cmp -s -- "${candidate}" "${mirror_file}"; then
  warn_and_continue "Post-write verification failed; inspect the runner before relying on the requested preference."
  exit 0
fi

if ! log_configuration "resulting configuration"; then
  warn_and_continue "The mirror file changed but could not be logged after replacement."
  exit 0
fi
log_effective_order
