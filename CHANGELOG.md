# Changelog

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
