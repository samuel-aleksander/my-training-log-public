# Package the training app for local download and run

## Goal
Give the user a single downloadable ZIP containing the whole app, plus clear
step-by-step instructions to run it on their laptop.

## Steps

1. Assemble a clean copy of the project under `/tmp`:
   - Include: `backend/` (app, tests, requirements.txt), `frontend/` (source,
     configs, package.json), `docs/` (the two Markdown plan files, unchanged),
     `README.md`.
   - Exclude: `node_modules`, `dist`, `.venv`, lockfile noise, build output,
     any database or uploaded FIT files from the sandbox.
2. Create `marathon-trainer.zip` in `/tmp`, inspect its entries, then copy it
   to `/mnt/documents` and present it as a downloadable artifact.
3. In chat, give the exact run steps (backend venv + uvicorn on port 8000,
   frontend `npm install && npm run dev` on 5173, open http://localhost:5173),
   including the Windows variant of the venv activation.
4. Verify the README inside the ZIP already covers install, startup, and
   backup/restore; patch it only if something is missing.

## Notes
- No app code changes; the app itself is already working and tested (22/22 tests).
- The plan Markdown files are copied verbatim — never modified.
