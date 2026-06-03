# Claude Code Instructions

***Important***: Do not commit files to git or push changes!

## Frontend Tests

After any UI change that adds, removes, or renames interactive elements (buttons, headings, nav links, modals), update the functional tests to match:

```bash
cd frontend && npx playwright test
```

Tests are in `frontend/tests/visual.spec.js`. They check element presence and visibility — not pixel snapshots — so they're stable across platforms without any snapshot files to maintain.

- All API calls are mocked — no backend needed
- Clock is frozen to `2026-06-03T10:00:00`
- 19 tests covering: app shell, today page, tasks board, notes, habits, quick-add modal
