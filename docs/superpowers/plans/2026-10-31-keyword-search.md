# T17 Keyword Search Implementation Plan

> **For agentic workers:** Use subagent-driven-development for independent tasks and spec/quality reviews; preserve all T16/ops dirty content. User authorized continuing the planned T17. No commit/push/deploy/paid call.

**Goal:** Search owned active note titles/tags/body and current OCR text, page safely and return to revalidated source text/image/region.

**Architecture:** Read-only SQL union of note matches and latest valid successful OCR scope. Bounded literal substring and owner-bound keyset cursor; independent resolve endpoint rechecks current source/version. Existing NotesWorkspace filters reuse a dedicated search result/source UI. No migration or new dependency.

**Tech Stack:** Existing SQLAlchemy/PostgreSQL, FastAPI, React Query/TypeScript, ImageReader, isolated pytest and synthetic browser UI.

## Task 1: Scope, baseline and environment

- [x] Inspect M2/T17 architecture, current notes query/cursor, OCR validity and frontend reader.
- [x] Record design and preserve current dirty worktree, main and no Git writes.
- [x] Start only compose.test disposable DB at55432/notes_test; migrate to existing7c head, baseline notes/OCR tests with fake provider.

## Task 2: Backend keyword service and contracts

Files: create `backend/app/modules/search/keyword_{schemas,service,router}.py`, `backend/tests/test_keyword_search.py`; modify main router and generated OpenAPI only. Shared validity helpers may be factored only if behavior preserved.

- [x] Write meaningful tests for query literal semantics, title/tag/body/OCR, newest successful per scope before keyword filter, corrections including empty, source invalidation, ownership, filters/cursor/resolve, and no paid/job side effects.
- [x] Build owner-scoped valid OCR candidate SQL with file/page/run/reading-variant checks and JSON type/region guards. Window rank by page/region, then text match.
- [x] Note candidate collapses title/tag/body with field priority; join only owned tags, preserve filters. SQL ASCII fold uses fixed translate strings, no wildcard SQL/interpolation.
- [x] Union compatible fields; order `(updated_at,kind,id)` descending, cutoff and query/filter/owner scope in strict reusable cursor decoder. Fetch limit+1; bounded snippets use code-point offsets.
- [x] Source endpoint selects valid current candidate, verifies expected revision/current OCR winner, returns controlled404/409 without secrets. Never return stale source or trigger provider.
- [x] Run new and relevant notes/OCR/AI tests sequentially on isolated DB.

Contract:
```typescript
type SearchHit={id:string;kind:'note'|'ocr';note_id:string;title:string;note_kind:'text'|'image';matched_field:'title'|'tag'|'body'|'ocr';snippet:string;match_start:number;match_end:number;match_hash:string;source_revision:number;ocr_revision:number|null;page_id:string|null;page_number:number|null;region:Region|null;reviewed:boolean|null;updated_at:string}
type SearchPage={items:SearchHit[];next_cursor:string|null;limit:number}
// GET /search?q=...&limit=20&cursor=...&kind=...&folder_id=...&notebook_id=...&tag_id=...
// GET /search/{kind}/{id}/source?q=...&source_revision=...&match_hash=...&ocr_revision=...
// Source: kind,id,note_id,title,matched_field,text,match_start,match_end,match_hash,source_revision,
// ocr_revision,page_id,page_number,file_id,region,reviewed. Text offsets in Unicode code points.
```

## Task 3: Frontend search/source workflow

Files: new `frontend/src/features/search/SearchResults.tsx`, source/helper/css/tests; targeted `NotesWorkspace.tsx` integration; generated API types after root exports.

- [x] Use existing q/filter controls for active notes, retain recycle-bin list behavior; disable original list request while global search active, don't show duplicate original cards.
- [x] Reset keyset navigation/source on query/filter/session change; show loading/empty/error states and meaningful field labels, bounded text highlights, latest/current OCR status.
- [x] Resolve clicked source afresh; plain text scroll-to-highlight and OCR text+ImageReader region; open current note action preserves existing editor workflow.
- [x] Guard stale/private responses; no model call/POST; actual desktop/mobile synthetic UI plus pure Unicode-highlight/navigation tests and build.

## Task 4: Integration/review/docs

- [x] Export OpenAPI and TS types, regenerate deterministically, Alembic check no migration differences.
- [x] Run full affected backend and frontend regression suites plus tool checks appropriate to no-schema/read-only change; investigate failures before fixes.
- [x] Independent spec review then quality/security review, resolve findings with regression.
- [x] Update M2 checklist, README current-status banner, search usage and task evidence, preserve M1 deferred boundary.
- [x] Final requirement-by-requirement audit, stop only own temporary test services, leave original data/Git/ops unchanged.

PowerShell commands from root:
```powershell
docker compose -f compose.test.yaml up -d --wait
$env:TEST_DATABASE_URL='postgresql+psycopg://notes:notes@127.0.0.1:55432/notes_test'
$env:DATABASE_URL=$env:TEST_DATABASE_URL
$env:APP_ORIGIN='http://testserver'
$env:SITE_BACKUP_TEST_CONTAINER='ai-notes-test-db-1'
Push-Location backend
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m pytest tests/test_keyword_search.py tests/test_notes.py tests/test_ocr.py -q
.venv/Scripts/python.exe -m scripts.export_openapi
.venv/Scripts/python.exe -m alembic check
Pop-Location
npm --prefix frontend run types
node --experimental-strip-types --test frontend/tests/*.test.mjs
npm --prefix frontend run build
git diff --check
```
