# v0.25 Live Test Log

## 26 October 2026

| Area | Status | Evidence |
|---|---|---|
| Developer console enabled warning at startup + /guide button for allowlisted owner | Verified live | terminal warning + screenshot |
| Console panel opens (category/action/target/Run/Recent audit) | Verified live | screenshot |
| practice_ready cooldown reset | Verified live | two practice cycles, +4 mastery each, audit entry |
| set_marks = 50 | Verified live | /inventory showed 50, audit logged |
| Heavy attack next turn staging | Partial | Heavy Strike seen turn 3 (8 dmg vs Lightning Guard); final outcome user-reported only |
| Non-owner denied / button reuse by another account | Verified live (user-reported) | 26 Oct, second Discord account: no console button in /guide; clicking owner's button refused; panel controls refused or not visible; non-disposable account not targetable; second account's /qi and /inventory normal with no developer mark; screenshots kept by owner. |
| Duplicate or concurrent actions on real PostgreSQL | Not tested | offline doubles only |
| Rollback on write error; audit persistence after restart | Not tested | |
| Companion, story, sect console actions | Not tested | |
| Story chapters, teacher lessons, Zhao duel | Not tested | |
| Two-player sect projects | Not tested | |

Offline suite: 1176 passed on 26 Oct (validate_offline.py).
