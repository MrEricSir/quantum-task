# Claude Code Instructions

## Visual Regression Tests

After any UI change (CSS, JSX, layout, etc.), update the visual snapshots so the baseline stays current:

```bash
cd frontend && npx playwright test --update-snapshots
```

Then commit the updated `frontend/tests/visual.spec.js-snapshots/` files alongside your code changes.

- Tests cover: desktop today, tasks, notes, habits; mobile today, notes, quick-add modal
- All API calls are mocked — no backend needed to run tests
- Clock is frozen to `2026-06-03T10:00:00` for stable date rendering
- macOS generates `*-darwin.png` snapshots; CI generates `*-linux.png` (separate baseline)
