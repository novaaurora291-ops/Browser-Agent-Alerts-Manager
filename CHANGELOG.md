# Hark Browser Manager changelog

Patch version goes up with every tested fix (hark_manager/version.py). The app shows `vX.Y.Z (short SHA)` in the title bar and the sidebar.

- 1.0.6 - Team Dispatch: Plan Preview shows each worker's Hark account, Cent profile, exact Hark project and git worktree; Real Hark is manual only (Open Project, Copy Task); Local Mock dispatch (Dispatch → Sent on confirmed delivery → Reply received only from the right account + project; account switch, duplicate and lost-ack cases covered); Live API stays BLOCKED without approved access; worker Hark account field; tests/test_team_mock_dispatch.py.
- 1.0.5 - Add Profile: Auto-fill date beside Birthday, with confirmed dates saved locally per account.
- 1.0.4 - SYNC_FROM_GITHUB.bat (safe sync of this folder's branch, SHA check against GitHub, launch from this folder); version + SHA shown in the app.
- 1.0.3 - 0ce8437 Live Multi-View: closed Cent window drops its pane, grid re-tiles, Open Profiles count updates; empty-grid fix.
- 1.0.2 - 45f3a48 Team Dispatch: Sent status survives restart; confirmed-failed worker retry after fresh Verify tabs.
- 1.0.1 - 238bef8 Team Dispatch: no card for the @planner block; Verify tabs + plan locked during Dispatch; prompt snapshot at the click.
- 1.0.0 - b7f1547 baseline.
