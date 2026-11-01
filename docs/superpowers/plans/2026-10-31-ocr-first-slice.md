# T16 OCR First Slice Implementation Plan

> **For agentic workers:** Use subagent-driven-development for independent implementation and specification/quality review. Preserve existing dirty files; do not commit, push, deploy or call a paid provider. User confirmed path A on 2026-10-31.

**Goal:** Deliver the approved manual single-image/PDF-page OCR, correction and source-location workflow including permission/version protection and export/restore compatibility.

**Architecture:** An owner-scoped `ocr_documents` record links one frozen page to an existing durable AiRun. Existing AI execution, single-attempt model transport and usage remain authoritative. OCR-specific status derives from that run and current source identity; corrections use an independent revision. React exposes a reusable panel from the selected page. Additive Alembic migration and versioned archive compatibility protect persistence.

**Tech Stack:** Existing FastAPI, SQLAlchemy/PostgreSQL, Alembic, Python project venv, React/TypeScript/Vite, existing model adapter and private-file reader; no new package.

## Task 1: Baseline and isolated verification environment

- [x] Preserve current dirty/untracked worktree; use current main per user's project instructions, no commits.
- [x] Start existing Docker Desktop if stopped, use only `compose.test.yaml` disposable PostgreSQL at 55432/notes_test. Never truncate notes_dev or source production.
- [x] Run existing AI image/PDF tests as baseline with fake model and isolated storage.

Commands from root (PowerShell):
```powershell
docker compose -f compose.test.yaml up -d --wait
$env:TEST_DATABASE_URL='postgresql+psycopg://notes:notes@127.0.0.1:55432/notes_test'
$env:DATABASE_URL=$env:TEST_DATABASE_URL
$env:APP_ORIGIN='http://testserver'
Push-Location backend
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m pytest tests/test_image_chat.py tests/test_pdf_chat.py -q
Pop-Location
```
Expected: original tests pass, no real provider/OSS request.

## Task 2: Backend OCR workflow

Files: create `backend/app/modules/search/{__init__,models,schemas,service,router}.py`, a new Alembic migration and `backend/tests/test_ocr.py`; extend shared model registration, main router, AI create/list/execute hooks only where required.

- [x] Add regression tests for owner-scoped create/list/get/edit, request replay/conflicting key, image/PDF whole/region, no configured model, activity/frequency limits, source changes, correction conflicts, stale worker/provider failures and no automatic retry.
- [x] Add OcrDocument with owner/run/note/page/file identities, source metadata, request identity, corrected_text/reviewed/revision/updated_at; composite owner FKs for run/note/file. Reference historical page ID without requiring deletion of OCR history on page removal.
- [x] Freeze using existing image/PDF context; build one-call transcription prompt with source as data. Same transaction creates internal conversation/run/document; hide internal conversations from normal chat listing and reject adding chat turns to them.
- [x] Preserve old chat request hashes. Add optional internal no-commit creation path to create_run; default chat behavior unchanged. Use owner lock and existing active/30-per-hour limits for OCR and chat together.
- [x] Expose the designed endpoints and controlled public output, no internal storage key. Return `source_valid`/reason and run status; raw_text only for succeeded runs. Correction requires succeeded/valid source and current OCR revision.
- [x] Validate OCR identity before calls and final progress persistence; source invalidation prevents result becoming current. Record failures without automatic paid retry. Reuse AiRun/AiStep usage once.
- [x] Run new regressions and original image/PDF/chat regressions; use same isolated test DB sequentially across all agents.

Contract for frontend:
```typescript
type OcrCreate = {note_id:string;page_id:string;revision:number;request_key:string;region?:Region|null}
// POST /ocr/documents -> OcrDocumentOut (201)
// GET /ocr/documents?note_id=...&page_id=...&offset=0&limit=20 -> OcrDocumentOut[]
// GET /ocr/documents/{id} -> OcrDocumentOut
// PATCH /ocr/documents/{id} {revision,corrected_text,reviewed} -> OcrDocumentOut
// Output: id,note_id,page_id,file_id,run_id,revision,status,error_code,raw_text,
// corrected_text,reviewed,source_valid,source_error,source_revision,page_number,
// region,model,usage,created_at,updated_at; exact generated types follow OpenAPI.
```

## Task 3: Frontend panel

Files: create `frontend/src/features/search/OcrPanel.tsx`, `ocr.css` and local helper tests as useful; modify `features/files/ImagePages.tsx` (ordinary/PDF converted pages share it) and generated contract after backend export.

- [x] Reuse RegionSelection and ImageReader; allow whole selected page or actual selected rectangle, explicit send confirmation and one request per intent.
- [x] List/poll active attempts, show queued/running/failure/truncation and plain identified source, preserve old corrected attempts on re-recognition. Never automatically POST from effects.
- [x] Expose raw transcript separately from correction textarea and reviewed checkbox, revision-safe save; error/conflict preserves edits.
- [x] Handle session/note/page changes and stale results, remove private content on auth switch. Reuse Query ownership and current-session guards.
- [x] Verify desktop/mobile layout, source area location, failures and no duplicate requests using local synthetic app, no paid model. Build and typecheck.

## Task 4: Persistence and compatibility

Files: existing account export format/service/verify/restore modules, `scripts/site_backup*` and related schema helpers as discovered; additive tests for new OCR records and legacy archives. Preserve original dirty monitoring_host/site_backup_schedule changes.

- [x] Add explicit archive version for OCR and support prior v1 archives. Include owned OCR correction/provenance/linked run; exclude credential/internal prompts/storage keys.
- [x] Validate relation ownership/source shape/revisions/content bounds, restore into empty DB, preserve run terminalization and no model replay. Test malformed/missing/cross-owner OCR references.
- [x] Add new exact schema profile/table allowlists for head while preserving c39/27-table and legacy c2 recovery paths. Update monitoring/backup regression expectations only when actually affected.
- [x] Run archive roundtrip and site-backup tests on the isolated PostgreSQL; no public runtime tool snapshot replacement.

## Task 5: Integration, review and evidence

- [x] Generate OpenAPI + TS contract, run Alembic check and full relevant regression suites sequentially; meaningful new backend/frontend tests, build, and git diff --check.
- [x] Independent spec review first, then quality/security review; resolve concrete findings and rerun affected checks.
- [x] Record actual UI verification and test counts, no real model/phone/public deployment claims. Update M2 plan, user instructions and stage evidence with migration/ops release compatibility notes.
- [x] Completion audit against all eight sections of the approved spec. Leave M1 deferred; no Git or public deployment operations.

Contract generation/build:
```powershell
Push-Location backend
.venv/Scripts/python.exe -m scripts.export_openapi
.venv/Scripts/python.exe -m alembic check
.venv/Scripts/python.exe -m pytest -q
Pop-Location
Push-Location frontend
npm run types
npm run build
Pop-Location
git diff --check
```
