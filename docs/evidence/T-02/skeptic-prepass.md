# T-02 step 0: skeptic pre-pass on T-01 (read-only subagent, 2026-11-01 19:40 ET)

Scope: `uaccess`, `exec`, the ISR/fault path, paging and ring-3 entry against INV-03 / INV-06 / INV-12 and
D-41, plus the card's known gap (a buffer that starts mapped and runs into the unmapped gap below `USER_TOP`).
The reviewer read the 14 listed files, disassembled `build/kernel.elf` and ran `nm`; nothing was edited.

## Findings and what T-02 did about them
| # | Severity | Where | Finding | Action (test) |
|---|---|---|---|---|
| 1 | gap (R5) | `uaccess.c` | `len == 0` returned 0 before any page walk, so `(0x50000000, 0)` and `(USER_TOP, 0)` passed. | Fixed: the walk always covers the page at `ptr`; `ptr >= USER_TOP` rejected. `badptr` cases 8, 9, 18; ktest `uaccess_bounds`. |
| 2 | gap (R3) | `paging.c` `paging_lookup` | Only `PDE.P` was checked; the PDE's U/W bits never were (not exploitable: user PDEs are always `P|W|U`). | Fixed: `paging_lookup` folds the PDE's U and W into the returned flags, so the `P|U(|W)` test covers both levels. ktest `uaccess_pde_bits`. |
| 3 | **latent bug** | `isr.S` | Nothing cleared DF on trap entry; ring 3 can run `std; int $0x80` and the kernel then runs with DF = 1 (also after an abandon). Not exploitable today only because every kernel copy is a byte loop; the first compiler-emitted `rep movs` would copy backwards into the trap stack. | Fixed: `cld` is the first instruction of `isr_common`. `dflag` test program (prints through `cons_write` with DF set). |
| 4 | gap | `badptr.c` | No case started in a mapped page and ran into an unmapped page below `USER_TOP`; cases 4, 5 and 8 did not test what their names said (rejected by the range checks before the walk). | Rewritten: 18 cases incl. the gap after `_mem_end` (read and write), null, `len == 0`, `ptr == USER_TOP`, a real wrap (`USER_TOP - 8, 4096`), and three valid edges that must succeed. |
| 5 | gap | `user/test/` | Only `cli` and a kernel write were covered for INV-06. | Added `null`, `hlt`, `ud`, `div0`, `stack`; `test_inv_06_fault_containment.py` asserts vectors 14, 13, 6, 0, 14. |
| 6 | nit | `interrupt.c` | NMI (2), #DF (8) and #MC (18) with CPL 3 in the saved CS were treated as user faults. | Fixed: those three vectors always panic. |
| 7 | nit | `paging.c` | Page 0 was identity-mapped, so a *kernel* null dereference silently succeeded (INV-12). | Fixed: PTE 0 cleared after building the identity map. |
| 8 | nit | `exec.c` | `cur.loop` sits right below the 8 KiB trap stack; an overflow would corrupt the saved context silently. | Canary word at the bottom of the trap stack, checked after every resume (panic = bug found). |
| 9 | nit | `interrupt.c` | Spurious IRQ 7 still gets an EOI. | Not changed (only IRQ 0 is unmasked; T-03b/T-04 unmask 3 and 4 and should add the ISR-register check). |
| 10 | nit | `exec.c` | Fault log omitted CR2. | Fixed: `cr2=` printed for vector 14. |
| 11 | nit | `exec.c` | A malformed embedded image panics. Acceptable while images are build-time constants. | Not changed; noted for any future loader. |
| 12 | nit | `exec.h` | `uprog.ro_len` / `mem_len` were never read. | Removed; the header is the only source. |
| 13 | nit | `serial.c` | Unbounded THRE wait with IF = 0. Not reachable in QEMU. | Not changed. |
| 14 | nit | `gdt.c` | Initial `esp0` was the boot stack (reset before every ring-3 entry anyway). | Not changed. |

## Confirmed OK by the reviewer
- No path from ring 3 to ring 0 except `int 0x80`: GDT DPLs, only vector 0x80 has DPL 3, unused vectors not
  present, identity map supervisor-only, `CR0.WP` set, `iret` frame EFLAGS 0x202 (IOPL 0), registers cleared on
  entry, user selectors restored on return, no SSE/MMX/x87.
- `uaccess`: `ptr <= USER_TOP` before the subtraction, every spanned page walked, second-page RO on write caught,
  no TOCTOU (IF = 0 in syscalls, one address space).
- R9 trap exit: EOI before the LIMIT abandon, exactly one EOI otherwise, `esp0` = dedicated trap stack,
  `kctx_save/resume` correct, frames freed and TLB flushed.
- INV-06 vectors as expected for all seven fault programs; the exception frame lands on `esp0` so no #DF.
- A ring-3 syscall loop cannot escape the tick quota; `frames[]` bounds and image/header checks hold.
