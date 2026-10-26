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
| Duplicate or concurrent actions on real PostgreSQL | Verified (automated, throwaway DB) | See "Real PostgreSQL integration (throwaway container, cases 1-12)": cases 1-5 and 8-12 |
| Rollback on write error; audit persistence after restart | Verified (automated, throwaway DB) | See "Real PostgreSQL integration (throwaway container, cases 1-12)": cases 6 and 7 |
| Real PostgreSQL integration (throwaway container, cases 1-12) | Verified (automated, throwaway DB) | 26 Oct: run_pg_cases_v025.py, all 12 cases passed as a non-superuser role in a removed dao-it container; commit "Add console integration cases 7-12 and record results" (harness from 9effbe5). Not a live Discord or sandbox-database test. |
| Companion, story, sect console actions | Not tested | |
| Story chapters, teacher lessons, Zhao duel | Not tested | |
| Two-player sect projects | Not tested | |

Offline suite: 1176 passed on 26 Oct (validate_offline.py); 1194 after the integration harness and its guard tests.
