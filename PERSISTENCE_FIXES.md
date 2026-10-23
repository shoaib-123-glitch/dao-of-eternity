# Persistence and concurrency fixes

Branch: `feature/fix-state-concurrency-and-qi`, based on local `develop`.

## Verified failures and fixes

The existing 218 tests passed before implementation. New offline handler tests
were then run against the original implementation to verify the reported bugs.

| Area | Observed before the fix | Change |
| --- | --- | --- |
| Combat and companions | A controlled battle-start/beast-advance interleaving acknowledged Stage 2 but saved Stage 1. Inventory was locked before companion state. | Battle startup reads companion state with `FOR UPDATE` before acquiring inventory. The lock stays held through settlement and saving. Existing combat-session locks and reward handling remain in place. |
| Sect trials | Both eligible and cooling-down requests raised `UnboundLocalError` at `datetime.now`. | Removed the conditional import that shadowed the module-level datetime names. Reward and cooldown values are unchanged. |
| Partnerships | Two concurrent acceptances sharing one proposer both became active. Opposite pending invitations could also both become active. | Ordinary and dual-session acceptances lock both household rows in ascending user-ID order before reading invitations or checking active partners. Existing rows are preserved; missing household rows are created using the existing schema. |
| Dual sessions | Opposite dual-session acceptances waited on each other's household locks until the test timeout. | Reuse the ordered household locks throughout acceptance, then acquire character locks in ascending order as before. Only one session grants boosts; the other encounters the existing cooldown. |
| Passive Qi | Secondary-root attunement credited 88 Qi rather than 90 for a hall activated halfway through a two-hour interval, or 96 for a Level 2 hall active throughout. | Use `Repository.settle_character`, including historical Gathering Hall windows, before setting the secondary root. Fractional Qi and overflow remain banked. |

No SQL schema definitions, balance constants, credential configuration, or bot
startup code were changed. There is no migration, record reset, or historical
data repair. Existing inconsistent records, if any, have not been inspected or
altered. Application-level locking protects these handlers; it does not impose
a new database uniqueness constraint on other writers.

## Automated validation

Run from the `dao_of_eternity_bot` folder using the installed dependencies:

```powershell
python -B -m unittest test_persistence_regressions -v
python -B -m unittest discover -v
```

Result: **233 tests passed**, comprising 218 existing tests and 15 new tests.
The local interpreter was Python 3.15; the existing GitHub workflow targets
Python 3.11, which was not separately exercised during this change.

The new tests call the real asynchronous handlers with mocked connections and
in-memory transaction-scoped row locks. They cover lost companion advancement,
lock ordering, battle turns, reward replay, missing companion state, sect-trial
rewards/cooldowns, competing partnerships, repeated acceptance, consent rejection,
opposing dual sessions, and Qi settlement with historical halls and overflow.

Neither environment file is read. Tests do not import the bot, call the
configuration loader, create a PostgreSQL pool, or use Discord credentials.

## Integration verification still required

These checks are documented but have not been run. They require a separately
authorized disposable database with synthetic players and the existing schema:

1. Use independent connections/processes to overlap battle startup with beast
   advancement, bond training, and exploration. Check that companion state and
   inventory survive and no lock-order deadlock occurs on those paths.
2. Race partnership acceptances with a shared proposer/recipient and with
   opposite invitations, for both existing and newly created household rows.
   Confirm only one active partnership and that conflicting requests roll back.
3. Race opposite dual-session acceptances. Confirm no deadlock, exactly one boost
   per player, and preservation of both household cooldowns after commit.
4. Verify transaction rollback and interaction replay around failures, including
   the finishing combat reward and sect-trial reward.
5. Exercise root attunement against actual hall-history and membership rows.
   Confirm effective-time boundaries, exact fractions/overflow, atomic inventory
   payment, and preservation of unrelated character fields.

The doubles do not execute SQL or model PostgreSQL isolation, MVCC, uniqueness
errors, or rollback. No database-dependent result is claimed here, and no schema
or data changes should be attempted to compensate for an unverified assumption.
