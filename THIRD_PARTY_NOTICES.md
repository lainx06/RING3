# Third-party notices

RING3's kernel, user programs, bridge, firmware and tests are written fresh in this repository (D-09). The only
third-party content is a set of **developer-tooling skills** (Markdown instructions for coding agents) vendored
byte-for-byte into `.claude/skills/` and `.agents/skills/` (plus the shared `references/` files three of them
link to). They are tools used while building RING3, not part of the project's code, and they run nothing: no
plugins, hooks, scripts or network access were installed (D-36). Byte-check against upstream:
`docs/evidence/T-01/skills-vendoring.txt`.

| Skill (vendored path under `.claude/` and `.agents/`) | Upstream | Commit | Licence | Copyright |
|---|---|---|---|---|
| `skills/test-driven-development/SKILL.md` | https://github.com/addyosmani/agent-skills (`skills/test-driven-development/SKILL.md`) | `aeac41d4d005999432bb7312f7ff06383a91d59d` | MIT (`LICENSE` beside it) | (c) 2025 Addy Osmani |
| `skills/debugging-and-error-recovery/SKILL.md` | same repo (`skills/debugging-and-error-recovery/SKILL.md`) | `aeac41d4…` | MIT | (c) 2025 Addy Osmani |
| `skills/code-review-and-quality/SKILL.md` | same repo (`skills/code-review-and-quality/SKILL.md`) | `aeac41d4…` | MIT | (c) 2025 Addy Osmani |
| `references/testing-patterns.md`, `references/security-checklist.md`, `references/performance-checklist.md` | same repo (`references/`), linked from the three skills above as `../../references/…` | `aeac41d4…` | MIT (`references/LICENSE`) | (c) 2025 Addy Osmani |
| `skills/grilling/` (`SKILL.md`, `agents/openai.yaml`) | https://github.com/mattpocock/skills (`skills/productivity/grilling/`) | `49dd158d1076134a641b33efb035946536778336` | MIT | (c) 2026 Matt Pocock |
| `skills/grill-with-docs/` (`SKILL.md`, `agents/openai.yaml`) | same repo (`skills/engineering/grill-with-docs/`) | `49dd158d…` | MIT | (c) 2026 Matt Pocock |
| `skills/ponytail/SKILL.md` | https://github.com/DietrichGebert/ponytail (`skills/ponytail/SKILL.md`) | `96608e078b2d5ea607c5c3b8b5a54d1c1fba6846` (upstream `main` at vendoring time, 2026-11-01) | MIT | (c) 2026 DietrichGebert |
| `skills/ponytail-review/SKILL.md` | same repo (`skills/ponytail-review/SKILL.md`) | `96608e07…` | MIT | (c) 2026 DietrichGebert |

Notes
- Only the files listed were taken. The ponytail **plugin** (two Node.js lifecycle hooks) is deliberately not
  installed; the other ponytail sub-skills (audit, debt, gain, help) and the rest of each upstream repo are not
  vendored.
- `grill-with-docs` upstream invokes a `domain-modeling` skill that is not vendored here; `grilling` works on its
  own.
- The `references/` checklists are web-application oriented (JS/TS examples); they are kept for completeness of
  the skills that link to them. For RING3 kernel code, D-09 (freestanding, nothing pasted) and the invariants win
  over any skill suggestion.
