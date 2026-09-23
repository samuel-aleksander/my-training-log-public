# Fix: week screen stuck on "Cargando semana…"

## What is happening

The week screen asks the backend for the week's data. If that request fails — the Python backend is not running, it is on a different port, or it returned an error — the screen has no way to say so: it just keeps showing "Cargando semana…" forever. So the message you see almost certainly means the request never succeeded, not that it is still loading.

Right now there is no way to tell which of those it is, because the error is silently discarded.

## What I will change

1. **Show the real problem instead of an endless "Cargando…".** If the week can't be loaded, the screen shows a short explanation (for example "Can't reach the backend at http://127.0.0.1:8000 — is it running?" or the exact message the backend returned), plus a "Retry" button.
2. **Same treatment on the Upload and Settings screens**, so no screen can hang silently.
3. **A small connection check on start-up**: the app pings the backend once and, if it's down, shows a one-line banner with the command to start it.
4. **Log the failure to the browser console** with the URL and status, so if it's not the backend being off, the cause is visible immediately.

Nothing about the plan, the data, the comparison rules or the stored files changes.

## Technical detail

- `frontend/src/lib/api.ts`: wrap `fetch` so network failures become a clear `Error` mentioning the backend URL; keep backend `detail` messages as-is.
- `frontend/src/pages/WeekView.tsx`, `Upload.tsx`, `Settings.tsx`: add `error` state next to the existing data state, `.catch(setError)` on every load, and render an error card with a retry action.
- `frontend/src/App.tsx`: one `/api/health` check on mount, banner when it fails.

## After the change

Reload the page. You will either see the week, or a message naming the exact failure — paste that here and I'll fix the underlying cause.
