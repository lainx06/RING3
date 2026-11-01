#!/usr/bin/env sh
# Prints toolchain versions and compiles a one-line freestanding object for i686-unknown-none-elf.
set -u
echo "== RING3 toolchain check =="
for t in clang ld.lld make qemu-system-i386 gdb python3 uv; do
  if command -v "$t" >/dev/null 2>&1; then
    printf '%-18s %s\n' "$t" "$("$t" --version 2>&1 | head -n 1)"
  else
    printf '%-18s MISSING\n' "$t"
    fail=1
  fi
done
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
printf 'int ring3_probe(void) { return 3; }\n' > "$tmp/probe.c"
if clang --target=i686-unknown-none-elf -ffreestanding -nostdlib -fno-pic -O2 -c "$tmp/probe.c" -o "$tmp/probe.o" 2>"$tmp/err"; then
  echo "freestanding i686-unknown-none-elf compile: OK"
else
  echo "freestanding i686-unknown-none-elf compile: FAILED"
  cat "$tmp/err"
  fail=1
fi
exit "${fail:-0}"
