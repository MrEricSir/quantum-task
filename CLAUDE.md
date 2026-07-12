# Claude Code Instructions

***Important***: Do not commit files to git or push changes!

## README.md

Keep this file up-to-date as the source of truth for how to configure and use this webapp. When asked for help with using the app, you should look here first for any relevant steps.


## Frontend Tests

After any UI change that adds, removes, or renames interactive elements (buttons, headings, nav links, modals), update the functional tests to match:

```bash
cd frontend && npx playwright test
```

Tests are in `frontend/tests/visual.spec.js`. They check element presence and visibility — not pixel snapshots — so they're stable across platforms without any snapshot files to maintain.

- All API calls are mocked — no backend needed
- Clock is frozen to `2026-06-03T10:00:00`
- 34 tests covering: app shell, today page, tasks board, notes, habits, quick-add modal (input + confirm screen), settings modals (tag manager, calendar settings), offline banner


## Gotchas

- The backend is designed to be run as-needed on Google Cloud Run, so the minimum instances must be 0.

### Timezone Handling

The server runs UTC (Cloud Run). All date/time logic must use the client's local clock. 

**Backend**: Always read timezone from the request via `deps.py` helpers — never from the request body:
- `local_date(request)` → client's local `YYYY-MM-DD` (from `X-Local-Date` header)         
- `utc_offset_minutes(request)` → JS-convention offset (UTC+10 → -600) (from `X-UTC-Offset` header)                                                                                   
                                                                                             
**Frontend**: Always use `apiFetch` from `api.js` — it injects both headers automatically. 
                                                                                        
If raw `fetch` is needed (e.g. SSE streaming with `AbortController`), manually add:        
```js                                                                                    
headers: { ..., 'X-Local-Date': localDate(), 'X-UTC-Offset': String(new                    
Date().getTimezoneOffset()) }                                                              
where localDate is imported from api.js. Never inline the date formatting logic.
