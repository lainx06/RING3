#!/usr/bin/env sh
# A8: tracked files must not contain Linux home-directory paths, Windows user-folder paths (including WSL's
# /mnt/c/Users form) or the local user name. This script excludes itself from the scan.
set -u
cd "$(dirname "$0")/.." || exit 2
me="tools/check-paths.sh"
user="${USER:-$(id -un)}"
# Patterns are assembled at run time so that this file never contains a literal match.
p_home='/home/[A-Za-z0-9_.-]+/'
p_win='[A-Za-z]:\\\\Users\\\\'
p_mnt='/mnt/[a-z]/Users/'
rc=0
files=$(git ls-files | grep -v -x "$me")
for pat in "$p_home" "$p_win" "$p_mnt"; do
  hits=$(printf '%s\n' "$files" | xargs -r grep -n -E -I -- "$pat" 2>/dev/null)
  if [ -n "$hits" ]; then
    echo "check-paths: pattern '$pat' matched:"; echo "$hits"; rc=1
  fi
done
if [ -n "$user" ]; then
  hits=$(printf '%s\n' "$files" | xargs -r grep -n -w -I -- "$user" 2>/dev/null)
  if [ -n "$hits" ]; then
    echo "check-paths: local user name found:"; echo "$hits"; rc=1
  fi
fi
[ "$rc" -eq 0 ] && echo "check-paths: OK ($(printf '%s\n' "$files" | wc -l) tracked files scanned)"
exit "$rc"
