# Changelog

## 0.4.0-beta.1 (2026-11-04)

- Rebuilt Dreaming core around V3 Event / Claim / Evidence / Profile, rather than V2 parallel observations and changes.
- Added per-source analysis ledger, dynamic token-bounded batches, independent cross-day profile consolidation, V3 replay and preview details.
- Added group-scoped V3 dashboard, claim evidence and reviewer operations, migration to schema 5 and consistent pre-V3 backup.
- Preserved old raw archive, V2 history, manual memory records and historical production cursor; old messages require deliberate history reanalysis.
- Syntax/static review only; live AstrBot/Hook/provider acceptance remains outstanding. No test code added.


## 0.3.0-alpha.2 (2026-11-04)

- Experimental V2 topic and observation extraction behind an opt-in switch.
- Transactional SQLite Schema v3 migration, preserving existing raw data and cursors.
- Persistent historical reanalysis jobs with independent progress, preview/apply and controls.
- Correct revision approvals; invalidate relevant derived data on member opt-out/erase.
- This branch has not been verified in a live AstrBot/Hook/provider deployment. Back up before use.

## 0.2.2 (2026-11-03)

- Fix Dreaming panel buttons blocked by sandboxed native confirmation dialog.

## 0.2.1 (2026-11-03)

- Discover read-only Hook Inbox path through the running adapter and improve backfill diagnostics.
