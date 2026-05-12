# Bulk Doc Converter — Project Specification

A FastAPI service that walks a folder of mixed-format documents (PDFs, scans, Office files, CSVs, plaintext) and emits a parallel tree of markdown files suitable for downstream knowledge-graph ingestion.

## How to read this document

This is a **spec-driven-development** specification. The intended reader is an engineer or AI agent with **zero prior knowledge** of the project who must be able to recreate the entire system from this document alone, without consulting the original source tree.

A few ground rules for using this spec:

- The spec captures **intent, contracts, and rationale**. The existing codebase is the *reference implementation* against which this spec was written, but it is not the source of truth.
- When the spec and the code conflict, the **spec wins for intent** and the **code wins for current observable behavior**. Disagreements should be surfaced and resolved before either side is changed.
- The reference test fixtures in **Appendix C** are the closest thing to a black-box conformance test for a recreation. A recreation is considered faithful when:
  - reference outputs for text-native kinds (`docx`, `xlsx`, `csv`, `text`) match **byte-for-byte**, and
  - reference outputs for OCR kinds match **structurally** (same page count, same headings, same table structure) — exact byte equality is not expected because OCR backends are non-deterministic across versions.
- Sections 1–11 describe *what* and *why*. The appendices describe *exactly what bytes go on disk*. A recreator should read 1, 2, 3, 5 first, then implement against 4 and 7, then verify against the appendices.

## Recreation playbook

A roadmap from "empty directory" to "passing smoke test". Details for each step live in sections 4–8; this list is intentionally terse.

1. Install Python 3.11.
2. Create a virtual environment and activate it.
3. Reproduce `requirements.txt` verbatim from Appendix B and `pip install -r requirements.txt`.
4. Reproduce the source tree from Appendix A — every Python module under `app/` and `app/services/`.
5. Reproduce `.env.example` and honor the env-var contract in Section 5.
6. Reimplement each module to the contract in Section 4 (`config`, `models`, `services/local_fs`, `services/textextract`, `services/docling_ocr`, `services/mistral`).
7. Implement the `POST /convert` endpoint to the contract in Section 2, using the routing rules in Section 3.
8. Implement the two OCR backends (Docling and Mistral) and the four text extractors (`docx`, `xlsx`, `csv`, `text`) to the output format spec in Section 7.
9. Reproduce the test fixtures described in Section 6 and by `scripts/gen_test_data.py`.
10. Run the smoke test from Section 6 and diff the output tree against Appendix C.
11. Once `/convert` is green, run `/graphify <output_dir> --obsidian --wiki` against the produced markdown to validate the Section 8 integration contract.

## Glossary

| Term | Meaning |
| --- | --- |
| OCR backend | A class implementing `convert(path) -> str` that turns a binary document (PDF or image) into markdown. Today: Docling (local) or Mistral (hosted). |
| Kind | The classification returned by `local_fs.classify(path)`. One of `ocr`, `docx`, `xlsx`, `csv`, `text`, `skip`. Drives extractor selection. |
| FileResult | The Pydantic record describing one file's outcome inside a batch response. Fields: `path`, `kind`, `status`, `output_path`, `reason`. See Section 2. |
| Page-wrapping markers | The Mistral-specific `[[START OF PAGE N]] ... [[END OF PAGE N]]` convention used to preserve page boundaries in OCR output. See Section 7. |
| Two-stage pipeline | `/convert` builds the markdown corpus; the external `/graphify` tool builds the knowledge graph from that corpus. The converter never builds graphs itself. See Sections 8 and 9. |
| PHI | Protected Health Information. Relevant because typical inputs are U.S. health-insurance documents; PHI risk drives the privacy-first defaults (local-first OCR, no telemetry, opt-in hosted backend). |
| SBC | Summary of Benefits and Coverage — a standardized member-facing benefits document and one of the primary input shapes this tool handles. |
| Graphify | The external knowledge-graph tool consumed downstream of `/convert`. Out of scope for this repo; documented separately. |

## Table of contents

1. Project identity & purpose
2. Functional requirements & API contract
3. File-type routing rules
4. Architecture & module reference
5. Setup, environment, and runtime
6. Testing & validation
7. Output format specification
8. Knowledge graph integration
9. Design decisions & rationale
10. Non-goals & constraints
11. Roadmap & extension hooks
12. Appendix A. File inventory
13. Appendix B. Pinned dependency list
14. Appendix C. Reference test-fixture outputs

## Spec metadata

- Commit: `10a1309b9414f900229da1d740ef6266effe9713`
- Date: `2026-05-12`
- Reference Python: `3.11`
- Reference platform: `Linux x86_64`

```
Files of record
- app/main.py            (FastAPI app; title "Bulk Doc Converter"; version 0.3.0)
- requirements.txt       (pinned stack: fastapi, uvicorn, pydantic, mistralai, docling, python-docx, openpyxl)
- git HEAD               10a1309b9414f900229da1d740ef6266effe9713
```

---

## 1. Project identity & purpose

### 1.1 One-line identity

`bulk-mistral-converter` is a single-operator, locally-run FastAPI service that walks a directory of heterogeneous source documents (PDF, scanned images, DOCX, XLSX, CSV, plain text, markdown) and emits a parallel directory of markdown files suitable for downstream knowledge-graph construction, RAG retrieval, and human review.

### 1.2 Elaborated description

The system is a thin orchestration layer over two responsibilities: classifying each file in an input tree by extension and dispatching it to the correct extractor. Text-native office formats (DOCX, XLSX, CSV) and already-textual formats (TXT, MD) are converted in-process using deterministic Python libraries. Image-bearing or page-rendered formats (PDF, PNG, JPG, JPEG, WEBP, TIFF, TIF) are routed through a pluggable OCR backend whose concrete implementation is selected at process start by an environment variable. Output is written to a mirror of the input tree under a configurable output root, with each source file replaced by a `.md` file at the same relative path.

The service exposes exactly two HTTP endpoints. `POST /convert` accepts a form-encoded `input_dir` and an optional `output_dir` (default `./out`) and processes the entire tree synchronously in a single request, returning a per-file result list. `GET /health` returns liveness and the currently active OCR backend name. There is no queue, no background worker, no persistence layer, no authentication: the service is intended to be launched, pointed at a folder, allowed to finish, and shut down.

`bulk-mistral-converter` is the first stage of a two-stage corpus pipeline. Stage one (this project) produces a clean markdown corpus on disk. Stage two is a separate `/graphify` command, invoked manually by the operator after stage one finishes, which consumes the markdown corpus and emits a knowledge graph plus an optional Obsidian vault. The two stages are intentionally decoupled: stage one is deterministic, idempotent, and cheap to rerun; stage two is iterative and may be re-run against the same corpus with different graph-construction parameters. The handoff between stages is a directory on disk, not an in-process call. The `/convert` response includes a `graphify_hint` string that names the exact follow-up command the operator should run; the service itself never invokes it.

The markdown corpus produced by stage one is the substrate for several downstream consumers: graph construction (entity and relation extraction over plan documents), retrieval-augmented generation for benefits Q&A, human reviewer workflows (open the `.md` next to the original), and agentic question-answering loops that need a normalized text representation of the source documents.

### 1.3 Domain context

PlanYear is a U.S. health-insurance benefits company. The documents this system processes are member-facing or broker-facing artifacts produced by carriers, employers, and brokers: plan summaries, Summary of Benefits and Coverage (SBC) documents, benefits grids, premium rate sheets, eligibility matrices, plan amendments, and miscellaneous policy notes. Source formats are whatever the issuing party happened to produce: carrier-generated PDFs (often a mix of text-native and scanned), broker-prepared spreadsheets, employer-shared DOCX summaries, and ad-hoc CSV or text exports.

Some of these documents contain, or are filed adjacent to documents that contain, Protected Health Information (PHI) as defined under HIPAA. This drives a hard privacy posture: in production use, document bytes must not leave the operator's machine. The OCR-backend abstraction exists in part to make this enforceable. The default backend (`docling`) runs entirely in-process and does not make outbound network calls for document content. The alternate backend (`mistral`) calls the Mistral OCR API and is intended for development, evaluation, and non-PHI workloads only. The choice is configuration, not a code change, and the operator is responsible for selecting a backend appropriate to the data being processed.

The privacy posture also drives architectural choices that may otherwise look austere: there is no telemetry, no cloud storage, no shared queue, no multi-tenant separation, and no authentication. The service is meant to be run by one operator on one machine against one input tree at a time. Any sharing of the resulting corpus is an explicit downstream action.

### 1.4 Problem statement

Benefits documents arrive in a long tail of formats. A single plan-year package for one employer may contain text-native PDFs, scanned PDFs that require OCR, image attachments, DOCX summaries, XLSX rate tables, CSV exports, and free-floating TXT notes. Each format has its own extraction story, its own failure modes, and its own structural fidelity tradeoffs. Downstream consumers (graph builders, retrievers, human reviewers, agents) cannot reasonably each implement seven extractors; they need a uniform input.

The problem this system solves is corpus unification: take a tree of heterogeneous documents and produce a tree of markdown files, one per source, at the same relative path, with content faithful enough that downstream systems can treat the markdown as the document. "Faithful enough" is format-dependent and is pinned in the output-format section of the full spec, but at minimum: text-native sources preserve text content byte-faithfully where the underlying library allows; tabular sources preserve cell order and row structure; OCR'd sources preserve heading structure, paragraph flow, and table cell ordering.

Out of scope for this system, by design:

- Entity extraction, normalization, or knowledge-graph construction. That is stage two (`/graphify`).
- Document classification beyond extension-based routing. The walker does not look inside files to decide what they are.
- Format conversion other than to markdown. The system does not produce HTML, JSON, plain text, or structured exports.
- Multi-document reasoning, deduplication, or cross-file linking.
- Long-running, queued, or distributed processing. One request, one tree, one response.

### 1.5 Audience for this spec

This spec is written for engineers and AI coding agents who have been handed the spec and asked to recreate the project from scratch, with no prior knowledge of the codebase, the company, or the prior conversations that produced it. A faithful reimplementation should be possible from this document alone. The existing source tree is the reference implementation and a tiebreaker for ambiguity, but the spec is the source of truth for what the system must be.

Readers are assumed to be fluent in Python 3.13, FastAPI, Pydantic v2, and the general shape of OCR and document-extraction libraries. Readers are not assumed to know anything about PlanYear, U.S. health-insurance document conventions, SBC documents, or the downstream `/graphify` stage beyond what this document states.

### 1.6 Success criteria for a faithful recreation

A recreation is considered faithful if and only if all of the following hold:

1. **Endpoint surface.** The HTTP surface consists of exactly the endpoints defined in section 2 of the full spec, with identical paths, methods, request schemas, and response schemas. No additional endpoints. No silently-renamed fields. `POST /convert` accepts `input_dir` (required) and `output_dir` (optional, default `./out`) as form fields. `GET /health` returns `{"status": "ok", "ocr_backend": <backend name>}`.
2. **Output fidelity.** Given the reference test fixtures (Appendix C of the full spec), output markdown matches the reference outputs byte-for-byte for text-native sources (DOCX, XLSX, CSV, TXT, MD), and structurally for OCR'd sources (PDF, image formats): table cells in correct row-major order, headings present at the correct level, paragraph flow preserved, no dropped pages.
3. **Environment-variable contract.** The set of environment variables, their default values, and the conditions under which each is required, exactly match section 3 of the full spec. At minimum: `OCR_BACKEND` (default `docling`, allowed values `docling` and `mistral`) and `MISTRAL_API_KEY` (default empty, required if and only if `OCR_BACKEND=mistral`). Variables are loaded from a `.env` file in the working directory via `python-dotenv` and `pydantic-settings`.
4. **Backend abstraction.** OCR backends are pluggable via the `OCR_BACKEND` environment variable. Any implementation that exposes a `convert(path: Path) -> str` method returning markdown is a valid backend. Adding a new backend must require no changes outside the backend factory and the backend module itself.
5. **Two-stage operating model.** The corpus-build endpoint (`/convert`) is synchronous and produces files on disk. Knowledge-graph construction is a separate, manually-invoked `/graphify` command outside this service. The `/convert` response includes a `graphify_hint` string naming the follow-up command. The service itself must not invoke `/graphify`, must not import graph-construction code, and must not depend on the graph-construction stage being installed.
6. **Idempotence and re-runnability.** Re-running `/convert` against the same input tree and output root must produce the same output tree. Existing output files may be overwritten; partial failures must not corrupt unrelated outputs.
7. **Failure isolation.** A failure on one input file must be reported in the `results` list with `status="failed"` and a `reason` string, and must not abort the request. Unsupported extensions produce `status="skipped"` with `reason` naming the extension.

### 1.7 Current state snapshot

The reference implementation as of the latest commit on `main` has the following exact identity:

- **FastAPI app `title`:** `Bulk Doc Converter`
- **FastAPI app `version`:** `0.3.0`
- **Swagger UI parameters:** `defaultModelsExpandDepth` set to `-1` (collapses model expansion in the docs page).
- **HTTP endpoints:**
  - `POST /convert` — synchronous corpus build. Form fields: `input_dir` (required string), `output_dir` (optional string, default `./out`). Returns `ProcessFolderResponse`.
  - `GET /health` — liveness probe. Returns `{"status": "ok", "ocr_backend": <settings.OCR_BACKEND>}`.
- **OCR backends wired in:**
  - `docling` — in-process, no network egress for document content. Implemented by `app.services.docling_ocr.DoclingService`.
  - `mistral` — calls the hosted Mistral OCR API. Implemented by `app.services.mistral.MistralService`. Requires a non-empty `MISTRAL_API_KEY`; raises `RuntimeError` at app construction if selected without a key.
- **Default OCR backend:** `docling` (set both in `app/config.py` as the `Settings.OCR_BACKEND` default and in `.env.example`).
- **Backend selection mechanism:** `_make_ocr_backend()` in `app/main.py`, called once at module import, dispatches on `settings.OCR_BACKEND.lower()`. Unknown values raise `RuntimeError` with the offending value in the message.
- **File kinds produced by the walker (`app/services/local_fs.py::classify`):** `ocr`, `docx`, `xlsx`, `csv`, `text`, `skip`. Extension routing:
  - `ocr`: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.tiff`, `.tif`
  - `docx`: `.docx`
  - `xlsx`: `.xlsx`, `.xlsm`
  - `csv`: `.csv`
  - `text`: `.txt`, `.md`, `.markdown`
  - `skip`: everything else
- **Walker traversal rules:** recursive `rglob("*")` from the resolved input root, sorted, files only, with any path containing a dotfile-prefixed segment (any `part.startswith(".")` in the relative path) excluded. This implicitly hides `.git/`, `.venv/`, `.DS_Store`, and similar.
- **Output file naming:** for each non-skipped input file with relative path `rel`, the output is written to `<output_dir>/<rel-with-suffix-.md>`. Parent directories are created as needed. The output extension is `.md`, not `.txt`, despite a recent commit message suggesting otherwise; the suffix in `app/main.py::_write_md` is `.with_suffix(".md")` and that is the binding contract.
- **Response model:** `ProcessFolderResponse` with fields `input_dir: str`, `output_dir: str`, `results: list[FileResult]`, `graphify_hint: str`. Each `FileResult` has `path: str` (relative to input root), `kind: str` (one of the walker kinds), `status: str` (one of `processed`, `skipped`, `failed`), `output_path: str | None` (relative to output root, set on success), `reason: str | None` (set on skip or failure).
- **`graphify_hint` content:** the exact string `Run `/graphify <output_dir> --obsidian --wiki` to build the knowledge graph + Obsidian vault.`, with `<output_dir>` substituted with the resolved absolute output path.
- **Runtime stack (from `requirements.txt`):** `fastapi==0.115.6`, `uvicorn[standard]==0.30.6`, `python-dotenv==1.0.1`, `pydantic==2.10.6`, `pydantic-settings==2.7.1`, `mistralai>=1.9.0`, `python-multipart==0.0.9`, `python-docx==1.1.2`, `openpyxl==3.1.5`, `docling>=2.0.0`.
- **Configuration loader:** `pydantic-settings` `BaseSettings` with `env_file=".env"`, `env_file_encoding="utf-8"`, `extra="ignore"`. Module-level `settings = Settings()` singleton in `app/config.py`.
- **Repository layout (top level):** `app/` (application package), `scripts/` (operator helpers), `test_data/` (fixture inputs), `out/` (default output root), `graphify-out/` (stage-two artifacts, not produced by this service), `spec_parts/` (this specification under construction), `.env.example`, `.gitignore`, `requirements.txt`. No `README.md` exists at the repository root; this specification is the project's primary written artifact.
- **Ignored from version control (`.gitignore`):** `.env` and `service-account.json`. The presence of `service-account.json` in the ignore list is historical; the current code does not read it. It is retained to prevent accidental check-in of cloud credentials from any future or operator-side integration.

### 1.8 What this spec does not include

This section establishes only project identity, purpose, domain context, and the high-level shape of a faithful recreation. It deliberately does not contain:

- The full HTTP API contract (request and response schemas in detail, error shapes, status codes). See section 2.
- The environment-variable contract in detail (every variable, every default, every required-when condition). See section 3.
- The system architecture, module boundaries, and dependency graph. See section 4.
- The walker's traversal semantics, ordering guarantees, and exclusion rules in spec-prose form. See section 5.
- Per-backend extractor behavior, library choices, and fidelity contracts (DOCX via `python-docx`, XLSX via `openpyxl`, OCR via `docling` or `mistralai`, etc.). See section 6.
- The output markdown format: heading conventions, table rendering, code block handling, image handling, and what "structural fidelity" means for OCR'd output. See section 7.
- The operational model: how the service is launched, how the operator drives it end to end, how `/graphify` is invoked after, and how reruns interact with existing output. See section 8.
- Design rationale: why two stages, why pluggable OCR, why synchronous, why no auth, why markdown. See section 9.
- Testing strategy, reference fixtures, and acceptance test definitions. See section 10 and Appendix C.

Subsequent sections of this document depend on the identity and contracts named here; later sections may refine but must not contradict them.

```text
Files of record
app/main.py
app/config.py
app/models.py
app/services/local_fs.py
requirements.txt
.env.example
.gitignore
```

---

## 2. Functional requirements & API contract

This section specifies the externally observable behavior of the Bulk Doc Converter service: every HTTP endpoint, every request and response field, the batch lifecycle of `POST /convert`, the per-kind processing contract, the shape of a per-file result, the difference between `skipped` and `failed`, the health endpoint, and the operational properties (idempotency, concurrency, non-streaming) that callers must rely on.

The reference implementation lives in `app/main.py`. Two Pydantic models in `app/models.py` (`FileResult`, `ProcessFolderResponse`) define the response wire format. A third model (`ProcessFolderRequest`) exists in the source tree but is **not** used by the live endpoint, which accepts `application/x-www-form-urlencoded` form fields rather than JSON; see Section 2.2.

### 2.1. API surface — complete listing

The service exposes exactly two HTTP endpoints. There is no authentication layer, no rate limiting, no versioned route prefix, and no other routes. The FastAPI app is constructed as:

- `title = "Bulk Doc Converter"`
- `version = "0.3.0"`
- `swagger_ui_parameters = {"defaultModelsExpandDepth": -1}` (collapses the model section of the auto-generated Swagger UI)

The auto-generated OpenAPI document is served at `/openapi.json`; the Swagger UI is served at `/docs`; the ReDoc UI is served at `/redoc`. These three paths are inherited from FastAPI defaults and are not explicitly configured.

| Method | Path       | Request media type                  | Success status | Purpose                                |
|--------|------------|-------------------------------------|----------------|----------------------------------------|
| POST   | `/convert` | `application/x-www-form-urlencoded` | 200            | Batch-convert a directory tree to `.md` |
| GET    | `/health`  | (none)                              | 200            | Liveness / smoke check                 |

#### 2.1.1. POST /convert — request

`POST /convert` accepts form-encoded fields. It does **not** accept a JSON body: the FastAPI handler declares both parameters with `fastapi.Form(...)`, which binds them to form data. A client sending `application/json` will receive a `422 Unprocessable Entity` from FastAPI's request validation layer.

| Field        | Type   | Required | Default  | Semantics                                                                                  |
|--------------|--------|----------|----------|--------------------------------------------------------------------------------------------|
| `input_dir`  | string | yes      | (none)   | Path to the directory tree to convert. May be absolute, relative, or `~`-prefixed.         |
| `output_dir` | string | no       | `./out`  | Path where the mirrored `.md` tree will be written. Same path-form rules as `input_dir`.   |

Path-form rules applied to both fields (see Section 2.2.1):

1. `Path(value).expanduser()` — `~` and `~user` are expanded.
2. `.resolve()` — converted to an absolute, normalized path (symlinks resolved, `..` collapsed).

The resolved paths are echoed back in the response so the caller can confirm what the server actually used.

#### 2.1.2. POST /convert — response

Response media type: `application/json`. The response model is `ProcessFolderResponse`.

| Field            | Type                | Optional | Semantics                                                                                          |
|------------------|---------------------|----------|----------------------------------------------------------------------------------------------------|
| `input_dir`      | string              | no       | The resolved absolute path of the input directory the server actually walked.                       |
| `output_dir`     | string              | no       | The resolved absolute path of the output directory the server actually wrote into.                  |
| `results`        | array of FileResult | no       | One entry per file discovered by the walk, in walk order (see Section 2.2.3). May be empty.        |
| `graphify_hint`  | string              | no       | Human-readable operator hint pointing to the next pipeline stage. See Section 2.2.6.               |

`FileResult` is defined in Section 2.4.

The response envelope contains no top-level counts, no timing data, no batch-level status, and no error array. All per-file outcomes — including failures — are reported inside `results`. The response **always** has HTTP status 200 if the batch ran to completion, regardless of how many individual files failed.

#### 2.1.3. POST /convert — error responses

The endpoint distinguishes two categories of failure:

1. **Pre-flight errors** raised before the batch loop begins. These return non-200 status codes:

| Status | Condition                                                                                   |
|--------|---------------------------------------------------------------------------------------------|
| 400    | `input_dir` (after expanduser + resolve) is not an existing directory.                       |
| 422    | Request body fails FastAPI form-field validation (e.g. `input_dir` missing or wrong type).   |
| 500    | OCR backend construction failed at process startup. See note below.                          |

   The 400 response body matches FastAPI's default error envelope: `{"detail": "Not a directory: <resolved-path>"}`. The 422 response is FastAPI's default validation envelope with a `detail` array. Both use `application/json`.

2. **Per-file errors** raised inside the batch loop. These do **not** propagate to the HTTP layer. They are caught by a broad `except Exception` and converted into a `FileResult` with `status="failed"` and `reason=str(e)`. The batch continues with the next file. See Section 2.2.5.

Process-startup note (status 500): the OCR backend is constructed **once at module import time** by `_make_ocr_backend()` (see `app/main.py`). If `OCR_BACKEND` is set to an unknown value, or if `OCR_BACKEND=mistral` is set with an empty `MISTRAL_API_KEY`, the import fails and the FastAPI process never reaches a serving state. There is no `/convert` request that can produce this error; the symptom is that the process exits before binding the port. Operationally this presents as a startup-time crash, not an HTTP error.

#### 2.1.4. GET /health — request and response

`GET /health` takes no parameters, no headers, no body. The response is a JSON object:

| Field          | Type   | Optional | Semantics                                                |
|----------------|--------|----------|----------------------------------------------------------|
| `status`       | string | no       | Constant literal `"ok"`.                                 |
| `ocr_backend`  | string | no       | Current effective value of `settings.OCR_BACKEND`.       |

Example: `{"status": "ok", "ocr_backend": "mistral"}`.

The status code is always 200 when the endpoint is reachable. There is no failure mode in the handler itself: if the FastAPI process is up, `/health` returns 200; if it is down, the client gets a connection error. See Section 2.6 for the intended uses.

### 2.2. POST /convert behavior — full batch lifecycle

The batch lifecycle is strictly sequential and runs entirely inside a single HTTP request. The handler function is synchronous (`def`, not `async def`) and FastAPI runs it on its threadpool.

The high-level sequence is:

1. Resolve `input_dir`. Reject if not an existing directory.
2. Resolve and create `output_dir` (parents included).
3. Walk the input tree to produce a sorted, filtered list of files.
4. For each file in order, route by `kind`, extract a body, and write a `.md` file. Catch all exceptions and record a per-file result.
5. Assemble and return `ProcessFolderResponse`.

#### 2.2.1. Input directory resolution

```
in_root = Path(input_dir).expanduser().resolve()
if not in_root.is_dir():
    raise HTTPException(400, f"Not a directory: {in_root}")
```

Rules:

- `expanduser()` is applied first, so `~/docs` becomes `/home/<user>/docs`.
- `resolve()` produces an absolute, normalized path. Symlinks **are** resolved; the walked tree is the symlink target's real location.
- The path **must already exist as a directory**. A non-existent path or a path pointing at a regular file produces `HTTPException(400)`. The error `detail` includes the resolved path so the caller can see exactly what the server tried to open.
- A relative input path is resolved against the **server process's current working directory**, not against the client's CWD. Clients should generally pass absolute paths.

#### 2.2.2. Output directory resolution

```
out_root = Path(output_dir).expanduser().resolve()
out_root.mkdir(parents=True, exist_ok=True)
```

Rules:

- Same expanduser + resolve treatment as `input_dir`.
- The directory is **created if missing**, including any missing parent directories. An existing directory is reused.
- If the path exists and is a regular file (not a directory), `mkdir` raises and the request aborts with `500 Internal Server Error` (uncaught exception). This is a misconfiguration case, not a normal error path.
- The default value is `"./out"`, resolved against the server's CWD. Callers relying on this default must understand where the server is running.

#### 2.2.3. File walk

The walk is delegated to `app.services.local_fs.walk(root)`. Rules, in order of application:

1. `Path.rglob("*")` enumerates every entry at every depth under `root`.
2. The full list is `sorted()` — lexicographic by `Path` comparison. This is the **walk order** and therefore the order of entries in `results`.
3. Non-files (directories, sockets, FIFOs, etc.) are skipped via `p.is_file()`.
4. **Dotfile filtering at any depth**: if **any** component of the path's relative path (relative to `root`) starts with `.`, the file is skipped. This excludes `.git/`, `.DS_Store`, `node_modules/.cache/foo.txt` (because `.cache` is a dotfile component), and so on. The root directory's own name is not considered.
5. Each surviving file is wrapped in a `LocalFile` dataclass with fields `path` (absolute), `rel` (relative to `root`), `ext` (lowercased suffix), and `kind` (one of `ocr | docx | xlsx | csv | text | skip` per Section 3).

The walk is eager: the entire file list is materialized before any processing begins. There is no streaming, no generator handoff, no incremental discovery.

After the walk, the handler emits a single diagnostic line to stdout:

```
FILES FOUND: <N> · OCR_BACKEND=<backend>
```

This is informational only; it is not part of the HTTP response.

#### 2.2.4. Per-file processing loop

For each `LocalFile f` in walk order, the loop performs exactly the following dispatch:

| `f.kind` | Body source                                  | Notes                                                                                  |
|----------|----------------------------------------------|----------------------------------------------------------------------------------------|
| `skip`   | (none)                                       | Append a `FileResult(status="skipped", reason=f"Unsupported extension: {f.ext}")`, continue. |
| `ocr`    | `ocr.convert(f.path)`                        | `ocr` is the singleton backend chosen at startup (`docling` or `mistral`).             |
| `docx`   | `extract_docx(f.path)`                       | From `app.services.textextract`.                                                       |
| `xlsx`   | `extract_xlsx(f.path)`                       | From `app.services.textextract`. Handles `.xlsx` and `.xlsm`.                          |
| `csv`    | `extract_csv(f.path)`                        | From `app.services.textextract`.                                                       |
| `text`   | `extract_text(f.path)`                       | From `app.services.textextract`. Reads the file as UTF-8 with `errors="replace"`.      |
| (other)  | `raise RuntimeError(f"Unknown kind: {f.kind}")` | Defensive; should be unreachable because `classify` only returns the six listed values. |

If `f.kind != "skip"`, the body string is written via `_write_md(out_root, f.rel, body)`. See Section 2.3.

The loop body is wrapped in `try / except Exception`. Any exception — including those raised by the OCR backend's network calls, file I/O errors, decoding errors in `python-docx`/`openpyxl`, or the defensive `RuntimeError` above — is caught and recorded as a `failed` result. The batch does not abort. See Section 2.2.5.

#### 2.2.5. Per-file failure isolation

Failure isolation is a hard requirement, not an implementation detail. The contract:

- An exception raised while processing file `X` must not affect the processing of any subsequent file `Y` in the walk.
- The exception must be converted into a `FileResult` with:
  - `path = str(f.rel)` — the input path relative to `in_root`.
  - `kind = f.kind` — the classification that was attempted (i.e. the kind whose handler raised).
  - `status = "failed"`.
  - `reason = str(e)` — the exception's `str()` representation. The full traceback is **not** included in the response; it may be logged separately by FastAPI/uvicorn but the wire format only exposes `str(e)`.
  - `output_path = None` — no output was written (or, if partially written, it is not reported).
- The loop must continue with the next file.
- If the output `.md` was partially created on disk before the exception, no rollback is attempted. Re-running the batch will overwrite or recreate it.

This applies to every `kind != "skip"`. A `skip` result is not a failure and is never produced via the exception path.

#### 2.2.6. Response assembly

After the loop completes, the handler builds:

```
hint = f"Run `/graphify {out_root} --obsidian --wiki` to build the knowledge graph + Obsidian vault."
```

and returns:

```
ProcessFolderResponse(
    input_dir=str(in_root),
    output_dir=str(out_root),
    results=results,
    graphify_hint=hint,
)
```

Notes:

- `input_dir` and `output_dir` in the response are the **resolved absolute paths**, not the raw form values. Callers can use them to confirm what the server actually opened and wrote to.
- `results` preserves walk order (Section 2.2.3). Clients that need a different ordering must sort client-side.
- `graphify_hint` is a string template. The current literal form interpolates the resolved output directory and references the `/graphify` slash command, `--obsidian`, and `--wiki` flags. The hint is opaque to the converter itself: it is metadata for the operator, not a structured next-step descriptor. Future revisions may change the wording; clients should not parse it.

The response is always returned with HTTP 200 if execution reached this point. There is no batch-level "all failed" → 5xx escalation; a batch in which every file failed still returns 200 with a `results` array of `failed` entries.

### 2.3. Per-kind processing contract

Each non-`skip` kind has a deterministic, single-function body extractor. The body is a UTF-8 string. The string is then written verbatim to the output `.md` path. There is no post-processing layer applied uniformly across kinds; the only post-processing in the codebase is page-wrapping inside the Mistral OCR backend (`app/services/postprocess.py`), which produces its body string before returning it.

#### 2.3.1. The `_write_md` convention

All processed files are written through:

```
def _write_md(out_dir: Path, rel: Path, body: str) -> Path:
    out_path = out_dir / rel.with_suffix(".md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    return out_path
```

Behavior, normative:

- The output path mirrors the input tree relative to the output root. `<in>/a/b/c.pdf` → `<out>/a/b/c.md`.
- The file extension is replaced with `.md` via `Path.with_suffix(".md")`. This applies to **every** processed kind, including inputs that are already markdown (`.md`, `.markdown`) and plain text (`.txt`). For inputs already named `*.md`, the output path equals the input path **only if the output directory equals the input directory** — a configuration the operator should avoid (see Section 2.7).
- Parent directories under `out_dir` are created on demand (`parents=True, exist_ok=True`).
- The file is written with `encoding="utf-8"` and `write_text` (which uses default `newline=None`, i.e. platform-default line endings translation on write). The body string itself is what each extractor produced; no normalization step is interposed.
- The returned `Path` is absolute. The handler converts it to a path **relative to `out_root`** before placing it in `FileResult.output_path`.

#### 2.3.2. Kind: `ocr`

- Selected when the lowercased extension is in `OCR_EXTS` (see Section 3.1).
- Body is produced by `ocr.convert(path) -> str`. `ocr` is a singleton chosen at process startup based on `OCR_BACKEND`:
  - `docling` (default): `DoclingService` uses `docling.document_converter.DocumentConverter` and returns `result.document.export_to_markdown()`.
  - `mistral`: `MistralService` uploads the file to Mistral's `/files` endpoint with `purpose="ocr"`, calls `client.ocr.process(model="mistral-ocr-latest", document={"type": "file", "file_id": ...}, table_format="html")`, then runs the result through `to_wrapped_markdown` in `app/services/postprocess.py`.
- Both backends expose the same method signature: `convert(self, path: Path) -> str`. The handler does not branch on backend; it only sees a `str`.
- Mistral-only body convention: pages are wrapped with `[[START OF PAGE n]]` / `[[END OF PAGE n]]` sentinels, table placeholders of the form `[tbl-N.html](tbl-N.html)` are inlined with the corresponding table HTML, and image markdown (`![...](...)` lines) is stripped. Docling output is not wrapped in page sentinels.

#### 2.3.3. Kind: `docx`

- Selected when the lowercased extension is in `DOCX_EXTS` (`.docx`).
- Body is produced by `extract_docx(path)`:
  - Iterates `doc.paragraphs`, stripping whitespace; non-empty paragraphs are kept.
  - Iterates `doc.tables`; for each row, joins cell texts with `" | "`.
  - Joins all parts with `"\n\n"` (blank-line-separated).
- No image extraction, no style preservation, no heading detection. Output is plain paragraph-and-pipe-table text.

#### 2.3.4. Kind: `xlsx`

- Selected when the lowercased extension is in `XLSX_EXTS` (`.xlsx`, `.xlsm`).
- Body is produced by `extract_xlsx(path)` via `openpyxl.load_workbook(data_only=True, read_only=True)`:
  - For each worksheet, iterates rows (`values_only=True`); replaces `None` with empty string; joins cells with `" | "`.
  - A row is included only if at least one cell has non-whitespace content.
  - Each non-empty worksheet contributes a block prefixed with `## Sheet: <title>`.
  - Blocks are joined with `"\n\n"`.
- `data_only=True` means cached evaluated values are read; formulas without cached results appear as empty.

#### 2.3.5. Kind: `csv`

- Selected when the lowercased extension is in `CSV_EXTS` (`.csv`).
- Body is produced by `extract_csv(path)`:
  - Opens with `encoding="utf-8"`, `errors="replace"`, `newline=""`.
  - Uses the stdlib `csv.reader` (default dialect: comma-separated, double-quote quoting).
  - Joins each row's cells with `" | "` and rows with `"\n"`.
- No header detection, no delimiter sniffing, no quoting normalization.

#### 2.3.6. Kind: `text`

- Selected when the lowercased extension is in `TEXT_EXTS` (`.txt`, `.md`, `.markdown`).
- Body is `path.read_text(encoding="utf-8", errors="replace")` — the raw file contents.
- The file is then re-written with extension `.md`. `.txt` files become `.md`; `.md` and `.markdown` files become `.md` (an identity rename for `.md`, a rename for `.markdown`).

#### 2.3.7. Kind: `skip`

- Selected for any extension not in the five non-skip sets. See Section 3.1.
- No body is computed and **no file is written to the output tree**.
- The result is `FileResult(path=str(rel), kind="skip", status="skipped", reason=f"Unsupported extension: {ext}")`.

### 2.4. FileResult contract

`FileResult` is the unit of per-file reporting. Its wire schema:

| Field          | Type            | Required | Default | Semantics                                                                                                            |
|----------------|-----------------|----------|---------|----------------------------------------------------------------------------------------------------------------------|
| `path`         | string          | yes      | (none)  | Input path **relative to the resolved `input_dir`**. Always set. Uses the OS path separator.                          |
| `kind`         | string          | yes      | (none)  | One of `ocr | docx | xlsx | csv | text | skip`. The classification assigned by `local_fs.classify` for this file.     |
| `status`       | string          | yes      | (none)  | One of `processed | skipped | failed`. See Section 2.5.                                                              |
| `output_path`  | string \| null  | no       | `null`  | Output path **relative to the resolved `output_dir`**. Set only when `status="processed"`. Null otherwise.            |
| `reason`       | string \| null  | no       | `null`  | Human-readable reason. Set when `status="skipped"` or `status="failed"`. Null when `status="processed"`.              |

Population rules, normative:

| `status`     | `output_path`                         | `reason`                                                              |
|--------------|---------------------------------------|------------------------------------------------------------------------|
| `processed`  | Set to the relative output path.       | `null`.                                                                |
| `skipped`    | `null`.                                | Set to `"Unsupported extension: <ext>"`.                              |
| `failed`     | `null`.                                | Set to `str(exception)` from the caught exception.                    |

`kind` is the **attempted** kind, including for `skipped` (always `"skip"`) and `failed` (whatever non-skip kind the handler was running when it raised). `kind` is **not** an enum on the wire — it is a `str` typed Pydantic field; clients should validate against the known set defensively.

`status` is likewise a `str` on the wire, not a Pydantic enum. The three values listed above are the only ones the server emits.

### 2.5. Skipped vs failed

These two terminal statuses must not be conflated by clients.

- **`skipped`** is a **routing decision** made before any processing is attempted. It is the outcome assigned by the classifier (`local_fs.classify` returns `"skip"`) for files whose extension is not in any handled set. No handler runs; no exception is raised; no output file is written. The `reason` is always of the form `"Unsupported extension: <ext>"` where `<ext>` is the lowercased suffix (including the leading dot, or empty string if the file has no extension).
- **`failed`** is a **runtime exception** raised by a handler for a kind that was supposed to be processed. The classifier accepted the file; the dispatcher invoked a real handler; the handler raised. The `reason` is `str(exception)`. Typical failure modes: corrupt input, network error from the Mistral API, encoding error inside `python-docx`/`openpyxl`, permission errors writing the output `.md`.

Both produce a non-null `reason` and a null `output_path`. The distinction in `status` is what tells the caller whether the file was deliberately ignored or whether processing was attempted and broke.

### 2.6. GET /health behavior

The handler is one line of logic; the contract is correspondingly narrow.

Response shape, normative: a JSON object with exactly two fields, `status` and `ocr_backend`, both strings. `status` is the constant `"ok"`. `ocr_backend` is the live value of `settings.OCR_BACKEND` — note this is the **configured** backend name, not a probe of its health.

Intended uses:

- **Smoke check / liveness probe** for a process supervisor or container orchestrator. A 200 means the FastAPI app is up and the configured OCR backend was constructed successfully at startup (because if it had failed, the process would never reach the serving state).
- **Configuration verification** during deployment: an operator can `curl /health` to confirm that the running process is using the expected backend.
- **Not** a readiness probe in the sense of guaranteeing that `/convert` will succeed for any given input; it does not invoke the OCR backend.
- **Not** authenticated. The endpoint is intended to be cheap, side-effect-free, and safely exposed to internal infrastructure. There is no rate limiting.
- **Cheap**: the handler performs no I/O, no model calls, and no file system access. It is safe to poll at high frequency.

### 2.7. Idempotency

Re-running `POST /convert` against the same `input_dir` with the same `output_dir` overwrites the output `.md` files in place. Specifically:

- `_write_md` calls `out_path.write_text(body, ...)` which truncates and rewrites the file unconditionally.
- Output directories are created with `exist_ok=True`; re-creation is a no-op.
- No record of the previous run is kept in the output tree. Stale outputs from files that have since been removed from the input tree are **not** cleaned up; they will persist until the operator deletes them.

The endpoint is therefore **idempotent for deterministic extractors**:

- `extract_docx`, `extract_xlsx`, `extract_csv`, `extract_text`, and `docling.DocumentConverter.convert` are deterministic in practice for a given input file and library version.
- For these kinds, re-running yields byte-identical output `.md` files.

The endpoint is **semantically idempotent but not byte-identical for OCR backends that are non-deterministic**:

- `MistralService.convert` calls a remote model; the model is not guaranteed to produce identical bytes across runs (whitespace, table formatting, occasional re-ordering can vary).
- Callers must not assume `output_path` contents are stable between runs when `kind="ocr"` and `OCR_BACKEND=mistral`.
- The structural contract (page sentinels, table inlining, image stripping; see `app/services/postprocess.py`) is preserved across runs.

Operator caution: setting `output_dir == input_dir` is allowed by the implementation but produces a degenerate state for `text` kind, where the input `.md`/`.markdown` would be overwritten by its own re-read contents (and `.markdown` renamed to `.md`). The service does not detect or prevent this; the operator should keep input and output trees separate.

### 2.8. Concurrency expectations

The batch loop is **single-threaded** and **sequential**:

- The handler function is a plain `def`. FastAPI dispatches it to its starlette threadpool.
- Within a single `/convert` request, files are processed one at a time in walk order. There is no `ThreadPoolExecutor`, no `asyncio.gather`, no internal parallelism.
- The OCR backend object is a process-global singleton (`ocr = _make_ocr_backend()` at module import time). It is shared across all in-flight `/convert` requests. The Mistral SDK client and Docling converter must be safe to reuse across threads; in practice the workload is dominated by per-call network or compute, not by client-object contention.

**Concurrent batches** are controlled at the process level by the FastAPI/uvicorn worker count:

- Running uvicorn with `--workers N` allows up to `N` concurrent `/convert` requests, each in its own process, each with its own OCR backend singleton.
- Within one worker process, FastAPI's threadpool allows multiple sync handlers to run concurrently in threads; this means two `/convert` calls to the same worker will execute their loops on different threads, sharing the same OCR singleton. Throughput in this configuration is bounded by the OCR backend's own concurrency limits (network, GIL for CPU-bound Docling work).
- There is no internal job queue, no cancellation API, and no in-progress status endpoint. Callers waiting on `/convert` see no progress until the response is returned.

### 2.9. No streaming

`POST /convert` is a fully-buffered request/response cycle:

- The entire batch must complete (every file processed, every `FileResult` appended) before any response bytes are sent.
- The response is a single JSON object serialized by FastAPI/Pydantic in one step.
- There is no chunked transfer of per-file results, no server-sent events, no websocket upgrade.

Caller obligations:

- HTTP client timeouts must be sized for the **worst-case batch duration**, not per-file duration. For OCR backends invoking remote models on large folders, this can run into many minutes.
- Reverse proxies and load balancers between client and server must be configured with matching idle/read timeouts, otherwise the connection will be killed before the server returns.
- Memory note: the full `results` array is held in memory until the response is serialized. For very large input trees (tens of thousands of files), the array size is bounded by `O(number_of_files * average_FileResult_size)`. The `body` strings themselves are not retained — each is written to disk and dropped from scope inside the loop iteration.

### 2.10. Environment variables touched in this section

The following environment variables affect `/convert` and `/health` behavior. Their full contract is specified in the configuration section of this document; the table here lists only what is observable through the API surface.

| Variable           | Default     | Observable effect                                                                                                |
|--------------------|-------------|-------------------------------------------------------------------------------------------------------------------|
| `OCR_BACKEND`      | `docling`   | Selects which class instantiates `ocr` at startup. Echoed back as `ocr_backend` in `/health`. Affects `kind=ocr` output. |
| `MISTRAL_API_KEY`  | `""`        | Required (non-empty) when `OCR_BACKEND=mistral`. If empty, the process fails at import time with a `RuntimeError`. |

Both are loaded via `pydantic-settings` from a `.env` file in the process CWD plus the OS environment, with environment taking precedence. Neither variable changes anything about the request/response wire format; they only change which OCR backend runs for `kind=ocr` files.

### 2.11. Worked example

A minimal client interaction, for grounding the schema:

Request (form-encoded):

```
POST /convert HTTP/1.1
Content-Type: application/x-www-form-urlencoded

input_dir=/data/sample&output_dir=/tmp/out
```

Assume `/data/sample/` contains: `a.pdf`, `b/c.docx`, `b/.skip.txt`, `notes.md`, `archive.zip`.

After walk + classify:

- `a.pdf` → `ocr`
- `b/c.docx` → `docx`
- `b/.skip.txt` → filtered out by the dotfile rule; never reaches the loop.
- `notes.md` → `text`
- `archive.zip` → `skip`

Response (HTTP 200, `application/json`):

```
{
  "input_dir": "/data/sample",
  "output_dir": "/tmp/out",
  "results": [
    {"path": "a.pdf",        "kind": "ocr",  "status": "processed", "output_path": "a.md",       "reason": null},
    {"path": "archive.zip",  "kind": "skip", "status": "skipped",   "output_path": null,         "reason": "Unsupported extension: .zip"},
    {"path": "b/c.docx",     "kind": "docx", "status": "processed", "output_path": "b/c.md",     "reason": null},
    {"path": "notes.md",     "kind": "text", "status": "processed", "output_path": "notes.md",   "reason": null}
  ],
  "graphify_hint": "Run `/graphify /tmp/out --obsidian --wiki` to build the knowledge graph + Obsidian vault."
}
```

Order in `results` follows the sorted walk order: `a.pdf`, `archive.zip`, `b/c.docx`, `notes.md`. If the OCR call on `a.pdf` had raised (e.g. network failure), the first entry would instead be `{"path": "a.pdf", "kind": "ocr", "status": "failed", "output_path": null, "reason": "<exception message>"}`, and the remaining three entries would still appear unchanged — failure isolation in action.

---

## 3. File-type routing rules

This section specifies how the walker decides which handler runs for each input file. The classification logic is centralized in `app/services/local_fs.py` and consists of: a small number of extension sets, a `classify(path)` function that maps a path to a kind, and a `walk(root)` function that enumerates and classifies all files under a root.

### 3.1. Extension-to-kind table

The mapping is defined by the module-level constants in `app/services/local_fs.py`:

| Kind   | Extensions (lowercased)                                | Constant      | Handler entry point                              |
|--------|---------------------------------------------------------|---------------|--------------------------------------------------|
| `ocr`  | `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.tiff`, `.tif` | `OCR_EXTS`    | `ocr.convert(path)` (Docling or Mistral singleton) |
| `docx` | `.docx`                                                  | `DOCX_EXTS`   | `extract_docx(path)`                             |
| `xlsx` | `.xlsx`, `.xlsm`                                         | `XLSX_EXTS`   | `extract_xlsx(path)`                             |
| `csv`  | `.csv`                                                   | `CSV_EXTS`    | `extract_csv(path)`                              |
| `text` | `.txt`, `.md`, `.markdown`                               | `TEXT_EXTS`   | `extract_text(path)`                             |
| `skip` | (anything not in the five sets above)                    | (none)        | (none — no handler runs)                         |

Notes on individual entries:

- `.tif` and `.tiff` are both included under `ocr`. Both are routed identically.
- Legacy office formats (`.doc`, `.xls`, `.ppt`, `.pptx`, `.odt`, `.ods`, `.rtf`) are **not** mapped; they fall through to `skip`. Callers that need them must pre-convert or extend the routing (Section 3.4).
- `.json`, `.yaml`, `.yml`, `.html`, `.htm`, `.xml`, `.log` are not mapped and fall through to `skip`.
- Files with **no extension** have `suffix == ""`, which is not in any set, so they classify as `skip` with `reason="Unsupported extension: "`.

`classify(path)` checks the sets in this exact order: `ocr → docx → xlsx → csv → text → skip`. The sets are disjoint, so order is not semantically meaningful, but it is the order in which a maintainer should read the function.

### 3.2. Case sensitivity

Extension matching is performed on the **lowercased suffix**:

```
ext = path.suffix.lower()
if ext in OCR_EXTS: ...
```

Implications:

- `FILE.PDF`, `file.Pdf`, and `file.pdf` are all classified as `ocr`.
- `Photo.JPG` is `ocr`.
- `Report.DOCX` is `docx`.
- `Data.CSV` is `csv`.

The sets themselves contain only lowercase entries. New extensions added to the sets must be added in their lowercased form, with a leading dot, or the membership test will fail silently.

The `ext` field of `LocalFile` and the `<ext>` interpolated into the `skipped` reason string are likewise the lowercased form. The original filename casing is preserved in `path` and `rel`.

### 3.3. Walk rules

`walk(root)` is the only file enumeration in the system. Normative rules:

1. **Root must be a directory.** The function calls `root.resolve()` and then raises `ValueError(f"Not a directory: {root}")` if `root.is_dir()` is false. (Note: the `/convert` handler has already validated this with its own 400 check; the `walk()`-level guard is defensive.)
2. **Recursive enumeration via `Path.rglob("*")`.** Every entry at every depth is considered.
3. **Sorted before filtering.** `sorted(root.rglob("*"))` sorts `Path` objects lexicographically. This sort fixes both the iteration order and the order of `FileResult` entries in the response.
4. **Files only.** Each candidate must satisfy `p.is_file()`. Directories, symlinks to directories (after `rglob` follows them is OS-dependent — in CPython, `rglob` does not follow directory symlinks by default), sockets, FIFOs, block devices, and character devices are excluded.
5. **Dotfile filtering at any depth.** For each surviving file, compute `p.relative_to(root).parts` and discard the file if **any** part starts with `.`. This is the only filtering applied beyond the file-vs-non-file check. Examples:

| Path (relative to root)                     | Filtered? | Reason                                  |
|---------------------------------------------|-----------|------------------------------------------|
| `report.pdf`                                | no        | No dotfile components.                  |
| `subdir/report.pdf`                         | no        | No dotfile components.                  |
| `.git/HEAD`                                 | yes       | `.git` component.                       |
| `subdir/.cache/x.txt`                       | yes       | `.cache` component.                     |
| `subdir/.DS_Store`                          | yes       | `.DS_Store` filename starts with `.`.   |
| `subdir/file.txt` (under a root literally named `.hidden/`) | no | The root's own name is not in `relative_to(root).parts`. |

6. **No size cap, no MIME sniffing, no content inspection.** Classification is purely by extension. A `.pdf` file containing arbitrary bytes is routed to the OCR backend; failure to parse becomes a `failed` `FileResult`.
7. **Output construction:** for each surviving file, a `LocalFile(path, rel, ext, kind)` is appended to the result list. `path` is absolute, `rel` is relative to `root`, `ext` is the lowercased suffix, `kind` is from `classify`.

The returned list is the exact iteration order used by the batch loop in `app/main.py`. The handler does not re-sort, re-filter, or re-classify.

### 3.4. Extensibility hook

To add a new file kind, the changes are local to three or four files. The system is intentionally not built around a plugin registry; the set of supported kinds is small and explicit.

The standard recipe:

1. **Decide whether the kind is text-native or OCR-like.**
   - Text-native: a deterministic library call extracts text/structure from a file format. Example: `.epub`, `.html`, `.rtf`.
   - OCR-like: requires running a vision/document model and produces page-structured output. Example: a new image format, a new scanner-output format.
2. **Extend or add an extension set in `app/services/local_fs.py`.**
   - For an extension that belongs with an existing kind (e.g. another image format under `ocr`), add the lowercased `.ext` literal to the existing `OCR_EXTS` / `XLSX_EXTS` / etc. set.
   - For a new kind, introduce a new module-level constant (e.g. `EPUB_EXTS = {".epub"}`) and a new branch in `classify()` checking it. Place the new branch in the same `if ... return` cascade.
3. **Add a handler.**
   - Text-native: add a new function in `app/services/textextract.py` with the signature `def extract_<kind>(path: Path) -> str`. Implement it using the appropriate library. Match the existing style: synchronous, no logging, returns a string.
   - OCR-like: add a new backend class in a new module under `app/services/` with the signature `def convert(self, path: Path) -> str`, matching `MistralService` / `DoclingService`. If the new backend is an alternative to (rather than in addition to) the current OCR backends, also extend `_make_ocr_backend()` in `app/main.py` to recognize the new `OCR_BACKEND` value and instantiate it. If the new backend handles a new kind rather than overlapping with `ocr`, expose it as a free function or a new singleton — do not multiplex it through the `ocr` slot.
4. **Add a `kind` branch in the batch loop in `app/main.py`.**
   - Inside the `for f in files:` loop, add an `elif f.kind == "<new-kind>":` branch that calls the new handler and assigns its result to `body`. The `_write_md` and `FileResult` assembly that follow are kind-agnostic and require no changes.
5. **Update Section 3.1 of this spec** so the kind→extension table stays authoritative.

What you do **not** need to change:

- `app/models.py`: `FileResult.kind` is a `str`, not an enum, so adding a new value does not require a schema migration. (You should still update the comment in `models.py` for the next reader.)
- `_write_md`: kind-agnostic; always writes `.md`.
- The walk, sort, and dotfile filter: kind-agnostic.
- `/health`: unaffected.

Design intent: the routing table is intentionally short and centralized. Each new kind is one extension-set entry, one classify branch, one extractor function, and one dispatch branch — five lines in three files in the common case. The system is not designed for dozens of kinds; if the matrix grows beyond ten or so, the maintainer should consider replacing the if-cascade with a dict-of-handlers pattern, but that refactor is out of scope for the current spec.

### 3.5 Upload pipeline endpoints (browser-facing surface)

The service exposes a second, additive surface on top of `POST /convert` for users that arrive via a web browser instead of having a server-local directory. The conversion logic is identical; the difference is in how files enter and leave the process.

#### `POST /upload`

| Field | Type | Required | Default | Semantics |
|---|---|---|---|---|
| `files` | multipart `list[UploadFile]` | yes | — | One or more uploaded documents. The `Content-Disposition` filename is preserved (after sanitization). |

| Response field | Type | Semantics |
|---|---|---|
| `job_id` | string (32-char hex UUID) | Identifier for polling and download. |
| `status` | string | Always `"queued"` on successful create. |
| `files_received` | integer | Count of files persisted to the job's `in/` directory. |

Status codes: `202 Accepted` on success; `400 Bad Request` if no files; `413 Payload Too Large` if `len(files) > MAX_FILES_PER_JOB` or aggregate body > `MAX_UPLOAD_BYTES`; `429 Too Many Requests` if rate limit exceeded.

Side effects:
- Generates a UUID, creates `${DATA_DIR}/jobs/<id>/in/` and `${DATA_DIR}/jobs/<id>/out/`.
- Writes each upload to `in/` with a sanitized filename (`_safe_filename` strips path separators, leading dots, and control characters; caps length at 200; resolves collisions with a numeric suffix).
- Asserts every written path resolves under the job's `in/` directory (path-traversal guard).
- Enqueues a FastAPI `BackgroundTask` that runs the standard `process_directory` against the job's directories.

#### `GET /jobs/{job_id}`

Polling endpoint. Status codes: `200` on found; `404` if unknown (including swept jobs).

| Response field | Type | Semantics |
|---|---|---|
| `job_id` | string | Echo of the URL parameter. |
| `status` | string | One of `queued | running | completed | failed`. |
| `created_at` | float (epoch seconds) | When the job was created. |
| `updated_at` | float (epoch seconds) | Last state transition (status change or result append). |
| `results` | `list[FileResult]` | Per-file outcomes. Populated incrementally during `running` so clients can render progress. |
| `error` | string \| null | Set only when `status == "failed"`. |
| `download_url` | string \| null | `"/jobs/<id>/download"` once `status == "completed"`; null otherwise. |

#### `GET /jobs/{job_id}/download`

Streams a `application/zip` archive of `${DATA_DIR}/jobs/<id>/out/`, preserving the relative tree.

Status codes: `200` (zip stream); `404` (unknown id, including swept jobs); `409 Conflict` (job exists but `status != "completed"`); `410 Gone` (job completed but zip already deleted by sweeper).

The zip's `Content-Disposition` filename is `<job_id>.zip`.

### 3.6 Browser UI endpoints

| Path | Method | Returns |
|---|---|---|
| `/` | GET | `app/templates/index.html` (Jinja2). Drag-and-drop upload form with per-file status table and download button. |
| `/static/*` | GET | Mounted via `StaticFiles`; serves `app/static/{style.css,app.js}`. |

The HTML page issues `POST /upload`, then polls `GET /jobs/{id}` every 2 s until status reaches a terminal state. On `completed` it surfaces the download URL as a button. No frontend build step; no framework; vanilla DOM and `fetch`.

### 3.7 Job lifecycle

```
queued ── BackgroundTask starts ──► running ──┬─► completed
                                              └─► failed (error message in `error`)
```

State transitions are written under a mutex on the in-memory `JobStore`. The job's `updated_at` advances on every transition and on every per-file result append.

A background sweeper thread runs every `max(60, JOB_TTL_SECONDS // 6)` seconds and removes any job whose `status` is `completed` or `failed` and whose `updated_at` is older than `JOB_TTL_SECONDS`. Removal deletes both the in-memory entry and the on-disk `jobs/<id>/` directory (recursively).

A job that is `queued` or `running` is never swept; if a job hangs for any reason it stays in memory until the next process restart.

### 3.8 Convert vs upload — when to use which

| | `POST /convert` (operator) | `POST /upload` (browser/API) |
|---|---|---|
| Caller | runs on same host as service | remote HTTP client, any |
| Input handoff | server-local directory path | multipart file upload |
| Output handoff | server-local directory path | zip stream after polling |
| Synchrony | blocking until done | 202 + polling |
| Auth | implicit (host-local trust) | none in current scope (single-tenant demo) |
| State | none | in-memory `Job` for the lifetime of the request + TTL |
| Intended audience | CLI scripts, cron jobs, the operator | end users, integrations from other services |

A recreation must implement both. Both call the same `app.services.runner.process_directory()` so behavior on identical inputs is guaranteed equivalent.

---

```
Files of record
- app/main.py
- app/models.py
- app/services/local_fs.py
- app/services/runner.py
- app/services/jobs.py
- app/services/zip_out.py
- app/config.py
- app/services/textextract.py
- app/services/mistral.py
- app/services/docling_ocr.py
- app/services/postprocess.py
- app/templates/index.html
- app/static/app.js
- app/static/style.css
```

---

## 4. Architecture & module reference

This section defines the runtime shape of the service: how the FastAPI
application, the filesystem walker, the per-kind dispatch, and the markdown
writer fit together; what contract each module exposes to the others; and
which discipline rules (lazy imports, failure isolation, separation of
concerns) MUST be preserved by any future re-implementation.

The system is intentionally small. There is exactly one HTTP entry point,
one dispatch table keyed by file kind, and one factory that selects an OCR
backend. There is no queue, no worker pool, no database, and no caching
layer. A future implementation MAY add those, but it MUST NOT change the
external contract or the in-process module boundaries described here
without a corresponding spec change.

### 4.1 Component diagram

The diagram below shows a single process serving a single `POST /convert`
request. "OCR backend" is the one implementation chosen at process startup
by the factory; the unchosen implementation's module (and its heavy
third-party deps) is never imported.

```text
            +----------------------------+
            |       FastAPI app          |
HTTP POST   |        (app/main.py)       |
/convert -->|  process_folder(form)      |
            |                            |
            |  1. resolve in/out roots   |
            |  2. validate in_root       |
            |  3. mkdir out_root         |
            +-------------+--------------+
                          |
                          v
            +----------------------------+
            |   local_fs.walk(root)      |
            |   (app/services/local_fs)  |
            |                            |
            |  - rglob, skip dotfiles    |
            |  - classify by suffix      |
            |  - emit list[LocalFile]    |
            +-------------+--------------+
                          |
                          v   for f in files:
            +----------------------------+
            |   per-file dispatch        |
            |        on f.kind           |
            +--+----+----+----+----+----+
               |    |    |    |    |    |
        ocr    |    |    |    |    |    |  skip
               v    |    |    |    |    |    v
        +----------+|    |    |    |    | +--------+
        |  ocr     ||    |    |    |    | | record |
        |.convert  ||    |    |    |    | |skipped |
        |  (...)   ||    |    |    |    | +--------+
        +----+-----+|    |    |    |    |
             |      |docx|xlsx|csv |text|
       (net? |      v    v    v    v    |
       only  | +-------+----+----+----+ |
       if    | |extract_docx          | |
       mistral|extract_xlsx           | |
       )    | |extract_csv            | |
             | |extract_text          | |
             | |(textextract.py,      | |
             | | all local)           | |
             | +----+-----+----+----+-+ |
             |      |     |    |    |   |
             +------+-----+----+----+---+
                          |
                          v body: str
            +----------------------------+
            |   _write_md(out_dir,...)   |
            |   (app/main.py)            |
            |  - mirror rel path         |
            |  - swap suffix to .md      |
            |  - mkdir parents, write    |
            +-------------+--------------+
                          |
                          v
            +----------------------------+
            |  assemble FileResult       |
            |  append to results list    |
            |  (try/except wraps loop    |
            |   body; exceptions become  |
            |   status=failed)           |
            +-------------+--------------+
                          |
                  end of for loop
                          |
                          v
            +----------------------------+
            | ProcessFolderResponse JSON |
            |  input_dir, output_dir,    |
            |  results[], graphify_hint  |
            +----------------------------+

Network legend:
  *  ocr.convert(...) touches the network ONLY when
     settings.OCR_BACKEND == "mistral" (calls api.mistral.ai).
  *  ocr.convert(...) is fully local when OCR_BACKEND == "docling"
     (Docling runs models locally; no outbound calls).
  *  extract_docx / extract_xlsx / extract_csv / extract_text are
     always fully local; they only touch the filesystem.
  *  local_fs.walk and _write_md are always fully local.
```

### 4.2 Request lifecycle

The numbered steps below describe a single `POST /convert` from arrival to
response flush. Steps 0a-0b happen once per process; steps 1-9 happen once
per request.

0. **Process import (once per process).**
   0a. `app/main.py` is imported. The very first statements call
       `load_dotenv()` so `.env` populates `os.environ` before
       `pydantic_settings` reads it.
   0b. `app/config.py` is imported, instantiating `settings`. `app/main.py`
       then calls `_make_ocr_backend()` at module top level, binding the
       module global `ocr` to either a `DoclingService` instance or a
       `MistralService` instance. The chosen backend's heavy dependency
       (`docling` or `mistralai`) is imported inside the backend's
       constructor, so it loads exactly once, at startup, in the chosen
       process only. The unchosen backend's module is never imported.

1. **Request arrives.** FastAPI dispatches `POST /convert` to
   `process_folder`. The form fields `input_dir` (required) and
   `output_dir` (default `"./out"`) are parsed.

2. **Path resolution.** Both directories are expanded (`~`) and resolved
   to absolute paths via `Path.expanduser().resolve()`.

3. **Input validation.** If `in_root` is not an existing directory, raise
   `HTTPException(400, ...)`. FastAPI converts this into a 400 response;
   no files are processed.

4. **Output preparation.** `out_root.mkdir(parents=True, exist_ok=True)`
   creates the output directory tree if absent.

5. **Walk + classify.** `walk(in_root)` returns a deterministically sorted
   `list[LocalFile]` where each entry carries the absolute `path`, the
   `rel`ative path under `in_root`, the lowercased `ext`, and the `kind`
   produced by `classify`. Dotfiles and dot-directories are excluded.

6. **Per-file loop.** For each `LocalFile f`, inside a `try`:
   - If `f.kind == "skip"`, append a `FileResult(status="skipped",
     reason="Unsupported extension: <ext>")` and continue.
   - Otherwise, select the body producer by kind:
     - `"ocr"` -> `ocr.convert(f.path)` (the module-global backend).
     - `"docx"` -> `extract_docx(f.path)`.
     - `"xlsx"` -> `extract_xlsx(f.path)`.
     - `"csv"` -> `extract_csv(f.path)`.
     - `"text"` -> `extract_text(f.path)`.
     - Anything else -> raise `RuntimeError("Unknown kind: ...")`.
   - Write the body via `_write_md(out_root, f.rel, body)`. The output
     path mirrors the relative input path with the extension replaced by
     `.md`. Parent directories are created on demand.
   - Append a `FileResult(status="processed", output_path=<rel under
     out_root>)`.

7. **Failure isolation.** Any `Exception` raised inside the loop body is
   caught; its `str()` becomes `FileResult.reason` and the file is
   recorded with `status="failed"`. The loop continues with the next
   file. No exception escapes the loop except via this handler. See
   section 4.5 for the rationale.

8. **Response assembly.** A `graphify_hint` string is built referencing
   the resolved `out_root`. The handler returns a
   `ProcessFolderResponse` Pydantic model. FastAPI serializes it to JSON
   and flushes the bytes to the client.

9. **Done.** No state is retained between requests. The next request
   re-walks, re-dispatches, and re-writes. The OCR backend instance,
   however, lives for the life of the process.

### 4.3 Backend abstraction protocol

The OCR backend is the only swappable component. It is defined by an
**implicit duck-typed protocol**, not by a formal `typing.Protocol` or ABC.
A class qualifies as an OCR backend if and only if it satisfies:

- Has a public method with the signature
  `convert(self, path: pathlib.Path) -> str`.
- The returned string is the **final, ready-to-write markdown body** for
  the file at `path`. The caller (`process_folder`) does no further
  transformation: it writes the returned string verbatim to disk.
- Any per-page wrapping, table inlining, image dropping, or other
  vendor-specific normalization is the backend's responsibility, not the
  caller's. (Mistral wraps pages with `[[START OF PAGE n]]` /
  `[[END OF PAGE n]]` markers via `postprocess.to_wrapped_markdown`;
  Docling produces a single flat document without page markers.)
- The backend MAY be constructed once per process and reused across
  requests. It MUST NOT cache per-file state across calls in a way that
  leaks one file's content into another file's result.
- `convert` MAY raise; the caller's `try/except Exception` will convert
  the failure into a `FileResult(status="failed")`.

The **single registration point** is the factory `_make_ocr_backend()` in
`app/main.py`. To add a third backend (e.g. `tesseract`), a new
implementation MUST be added under `app/services/`, and the factory MUST
be extended with a new branch keyed on the lowercased value of
`settings.OCR_BACKEND`. Nothing else in the codebase references backend
classes directly; the module global `ocr` is the only handle.

### 4.4 Lazy-import discipline

Both OCR backends defer their heavy third-party imports until the backend
is actually instantiated:

- `DoclingService.__init__` imports `from docling.document_converter
  import DocumentConverter` inside the constructor body.
- `MistralService` imports `mistralai` at module top, but `MistralService`
  itself is only imported from `app/main.py:_make_ocr_backend()` inside
  the `backend == "mistral"` branch. Therefore `mistralai` only loads
  when the Mistral branch is taken.

This is **not stylistic**. The constraint is operational:

- A process started with `OCR_BACKEND=docling` MUST NOT import
  `mistralai`. The Mistral SDK pulls a long network/HTTP stack that is
  unnecessary in pure-local mode.
- A process started with `OCR_BACKEND=mistral` MUST NOT import `docling`.
  Docling pulls model-loading code (PyTorch, transformers, etc.) that is
  multi-hundred-megabyte and slow to import; loading it for a network-
  only deployment is wasted memory and startup latency.
- The factory selects exactly one branch and only that branch executes
  its `from ... import ...` line. The unchosen module is never touched.

A future contributor adding a new backend MUST follow the same pattern:
heavy deps go inside the constructor (or method body), and the only
top-level imports allowed are the standard library plus in-project
modules that themselves are lazy.

### 4.5 Failure-isolation pattern

The per-file loop in `process_folder` is wrapped in
`try/except Exception`. The exception's `str()` becomes
`FileResult.reason`; the file is recorded with `status="failed"`; the
loop proceeds to the next file. This is deliberate and MUST be
preserved:

- A bulk-conversion call over a real corpus will routinely encounter
  individual files that cannot be processed: corrupt PDFs, password-
  protected DOCX, XLSX with broken external links, Mistral API quota
  errors on a single document, OOMs on a Docling layout pass for one
  pathological page. The cost of letting one such file abort the whole
  request is too high.
- The exception type is `Exception` (not `BaseException`); KeyboardInterrupt
  and SystemExit still propagate.
- The catch is at the granularity of one file, not one batch. There is no
  retry. The caller sees the per-file outcome in `results[]` and decides
  whether to re-submit.
- Logging beyond the recorded reason is not required by the spec; an
  implementation MAY add structured logging without changing the
  contract.

### 4.6 Separation of concerns

The module boundaries are tight and one-directional. A reimplementation
MUST preserve them:

- **`app/services/local_fs.py`** owns *the filesystem and classification*.
  It knows what an extension means, what a dotfile is, and how to
  enumerate a tree deterministically. It returns plain data
  (`LocalFile`). It does not read file contents and does not know
  anything about markdown.
- **`app/services/textextract.py`** owns *text-native file decoding*. One
  small function per supported kind. It takes a path and returns a
  markdown string. It does not classify, does not walk, and does not
  write output. Its imports for heavy parsers (`python-docx`,
  `openpyxl`) are function-local to keep import time cheap.
- **`app/services/mistral.py` and `app/services/docling_ocr.py`** own
  *image/PDF -> markdown via a model*. Each knows exactly one vendor's
  API. Neither knows about other file kinds. Neither writes output.
- **`app/services/postprocess.py`** owns *Mistral-specific output
  normalization*: dropping image references, inlining HTML tables stored
  under stable `tbl-N.html` ids, and wrapping each page with
  `[[START OF PAGE n]] ... [[END OF PAGE n]]`. It is Mistral-only by
  design; Docling output is already a single normalized document.
- **`app/models.py`** owns the *wire contract*. Pydantic models for
  request, per-file result, and aggregated response. Nothing else.
- **`app/config.py`** owns *environment-derived settings*. A single
  `Settings` instance, loaded once.
- **`app/main.py`** is the *only orchestrator*. It wires the FastAPI
  app, builds the OCR backend via the factory, and runs the per-file
  loop. It is the only module that imports from more than one of the
  above. No service module reaches across boundaries into another
  service module, except `mistral.py` -> `postprocess.py`, which is
  intentional and one-directional (Mistral output -> Mistral post-
  processing; postprocess does not import mistral).

A picture of the allowed import graph:

```text
              main.py
            /   |   |   \
       config  models  local_fs  textextract
                          (leaf)   (leaf)
              \   |
               \  +---> docling_ocr      (leaf)
                \
                 +----> mistral ---> postprocess
                                     (leaf)
```

`main.py` is the only node with out-degree > 2. Every leaf has out-degree
0 (within the project; third-party imports are out of scope for this
graph).

### 4.7 Module reference

Each subsection documents one module. Signatures are given in code style;
function bodies are not reproduced. Where the algorithm is non-trivial
(e.g. `walk`, `_make_ocr_backend`, `to_wrapped_markdown`), a prose
summary is included.

#### 4.7.1 `app/main.py`

- **Path**: `app/main.py`
- **Purpose**: HTTP entry point; constructs the FastAPI app, selects the
  OCR backend at import time, and orchestrates the per-file convert
  loop.

**Public interface**

- `app` (module-level): `FastAPI(title="Bulk Doc Converter", version="0.3.0", ...)`.
  The ASGI application object that uvicorn (or any ASGI server) mounts.
  Created at import time. Swagger UI is configured with
  `defaultModelsExpandDepth=-1` so the models section is collapsed by
  default.

- `ocr` (module-level): the OCR backend instance returned by
  `_make_ocr_backend()`. Bound exactly once, at import time. Used by the
  request handler as `ocr.convert(path)`.

- `_make_ocr_backend() -> object`
  Factory that returns an OCR backend instance based on
  `settings.OCR_BACKEND.lower()`.
  - `"docling"`: lazily imports `DoclingService` from
    `app.services.docling_ocr` and returns `DoclingService()`.
  - `"mistral"`: requires `settings.MISTRAL_API_KEY` to be non-empty;
    raises `RuntimeError("OCR_BACKEND=mistral but MISTRAL_API_KEY is
    empty")` otherwise. Lazily imports `MistralService` from
    `app.services.mistral` and returns `MistralService(api_key)`.
  - Any other value: raises `RuntimeError(f"Unknown OCR_BACKEND:
    {backend}")`.
  This is the only call site that knows about concrete backend classes.
  Although named with a leading underscore, it is the de-facto
  registration point and the only mechanism for adding a new backend.

- `_write_md(out_dir: Path, rel: Path, body: str) -> Path`
  Computes `out_dir / rel.with_suffix(".md")`, creates the parent
  directory tree (`mkdir(parents=True, exist_ok=True)`), writes `body`
  as UTF-8, and returns the resulting absolute path. Side effect:
  filesystem write. No exception is caught here; I/O errors propagate
  to the caller (and are converted to `FileResult(status="failed")` by
  the loop's `try/except`).

- `process_folder(input_dir: str = Form(...), output_dir: str = Form("./out")) -> ProcessFolderResponse`
  FastAPI route handler bound to `POST /convert`. Receives form-encoded
  body fields. Walks the input directory, dispatches each file by
  `kind`, writes the markdown output, and returns the aggregated
  response. Raises `HTTPException(400)` if `input_dir` is not a
  directory. Per-file failures are caught and recorded; the request
  itself only fails if the input directory is invalid.

- `health() -> dict`
  FastAPI route handler bound to `GET /health`. Returns
  `{"status": "ok", "ocr_backend": settings.OCR_BACKEND}`. No side
  effects.

**Internal dependencies**

- `app.config` (`settings`)
- `app.models` (`ProcessFolderResponse`, `FileResult`)
- `app.services.local_fs` (`walk`)
- `app.services.textextract` (`extract_docx`, `extract_xlsx`,
  `extract_csv`, `extract_text`)
- `app.services.docling_ocr` (`DoclingService`, lazily, only if
  backend is docling)
- `app.services.mistral` (`MistralService`, lazily, only if backend is
  mistral)

**External dependencies**

- `python-dotenv` (`load_dotenv`)
- `fastapi` (`FastAPI`, `Form`, `HTTPException`)
- `pathlib` (stdlib)

**Invariants**

- `load_dotenv()` MUST be the first executable statement of the module,
  before `app.config` is imported, so that `.env` values populate
  `os.environ` before pydantic-settings reads them.
- `_make_ocr_backend()` is called exactly once per process, at import
  time. The result is the module global `ocr`.
- The dispatch table inside the loop MUST cover every value emitted by
  `classify` except `"skip"` (which is handled separately). An unknown
  `kind` raises `RuntimeError`, which the loop's `except` records as a
  failure.

**Error modes**

- `HTTPException(400)` if `input_dir` is not a directory.
- `RuntimeError` from `_make_ocr_backend()` at import time (not request
  time) if the backend setting is invalid or Mistral key is missing.
- Per-file exceptions never escape the loop.

**Notes**

- The `print(f"FILES FOUND: ...")` line is debug-grade logging; a
  reimplementation MAY replace it with structured logging.
- The `graphify_hint` is informational only; it is not enforced or
  validated.
- There is no streaming response: the entire batch result is buffered
  before the response is sent. For very large folders this MAY become a
  memory concern; out of scope for v0.3.0.

#### 4.7.2 `app/config.py`

- **Path**: `app/config.py`
- **Purpose**: Process-wide configuration loaded from environment and
  `.env`.

**Public interface**

- `Settings(BaseSettings)`
  Pydantic-settings model. Fields:
  - `OCR_BACKEND: str = "docling"` — accepted values
    `"docling" | "mistral"` (case-insensitive at the factory).
  - `MISTRAL_API_KEY: str = ""` — required only when
    `OCR_BACKEND == "mistral"`.
  `model_config` sets `env_file=".env"`, `env_file_encoding="utf-8"`,
  `extra="ignore"`. Extra env vars are silently ignored so the file can
  carry unrelated app secrets without breaking import.

- `settings: Settings`
  Module-level singleton, constructed at import. All consumers import
  `settings`, not `Settings`.

**Internal dependencies**

- None.

**External dependencies**

- `pydantic-settings` (`BaseSettings`, `SettingsConfigDict`)

**Invariants**

- `settings` is constructed exactly once at import. It is treated as
  immutable at runtime; nothing in the codebase mutates it.
- The default `OCR_BACKEND` is `"docling"`, so a fresh install with no
  `.env` runs fully local out of the box.

**Error modes**

- Pydantic validation errors at import time if a future field is added
  with a required type that the environment cannot satisfy. v0.3.0 has
  no required fields.

**Notes**

- No secrets logging. Do not add `__repr__` overrides that print
  `MISTRAL_API_KEY`.

#### 4.7.3 `app/models.py`

- **Path**: `app/models.py`
- **Purpose**: Pydantic schemas for the HTTP wire contract.

**Public interface**

- `ProcessFolderRequest(BaseModel)`
  - `input_dir: str`
  - `output_dir: str = "./out"`
  Currently unused by the route handler (the handler reads `Form`
  fields directly), but retained for clients that prefer to build a
  typed body and for OpenAPI documentation consumers.

- `FileResult(BaseModel)`
  - `path: str` — the file's path relative to `input_dir`.
  - `kind: str` — one of `"ocr" | "docx" | "xlsx" | "csv" | "text" |
    "skip"`.
  - `status: str` — one of `"processed" | "skipped" | "failed"`.
  - `output_path: str | None = None` — the output `.md` path relative
    to `output_dir`. Set only when `status == "processed"`.
  - `reason: str | None = None` — human-readable explanation. Set when
    `status` is `"skipped"` or `"failed"`.

- `ProcessFolderResponse(BaseModel)`
  - `input_dir: str` — resolved absolute path.
  - `output_dir: str` — resolved absolute path.
  - `results: list[FileResult]` — one entry per discovered file
    (including skips and failures).
  - `graphify_hint: str` — a suggested follow-up command for users of
    the companion `graphify` skill. Informational only.

**Internal dependencies**

- None.

**External dependencies**

- `pydantic` (`BaseModel`)

**Invariants**

- `kind` and `status` are plain `str`, not `Enum`. The set of legal
  values is enforced by the producer (`local_fs.classify` and
  `process_folder`), not by the schema. A re-implementation MAY tighten
  this with `Literal[...]` types without breaking compatible clients.

**Error modes**

- Pydantic raises `ValidationError` if a consumer constructs a model
  with missing required fields. The route handler only constructs
  valid instances, so callers never see this in production.

**Notes**

- The schema MUST remain JSON-serializable; no custom field types
  beyond stdlib primitives.

#### 4.7.4 `app/services/local_fs.py`

- **Path**: `app/services/local_fs.py`
- **Purpose**: Recursively enumerate files under a directory,
  classifying each by extension into a dispatch `kind`.

**Public interface**

- Module constants (frozenset-like `set[str]` of lowercased
  extensions, each leading dot included):
  - `OCR_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif"}`
  - `DOCX_EXTS = {".docx"}`
  - `XLSX_EXTS = {".xlsx", ".xlsm"}`
  - `CSV_EXTS = {".csv"}`
  - `TEXT_EXTS = {".txt", ".md", ".markdown"}`

- `@dataclass class LocalFile`
  Fields:
  - `path: Path` — absolute, resolved.
  - `rel: Path` — relative to the walk root.
  - `ext: str` — lowercased suffix (including the leading dot), or `""`
    for extensionless files.
  - `kind: str` — one of `"ocr" | "docx" | "xlsx" | "csv" | "text" |
    "skip"`.

- `classify(path: Path) -> str`
  Returns the dispatch kind for a path based on `path.suffix.lower()`.
  Membership is checked against the module constants in fixed order:
  ocr, docx, xlsx, csv, text. Anything else returns `"skip"`. Pure
  function; no I/O.

- `walk(root: Path) -> list[LocalFile]`
  Algorithm:
  1. `root = root.resolve()`.
  2. If `root` is not a directory, raise `ValueError(f"Not a directory:
     {root}")`.
  3. Iterate `sorted(root.rglob("*"))` (sorted for deterministic
     ordering).
  4. Skip entries that are not files.
  5. Skip entries whose **relative** path has any component starting
     with `"."` (dotfiles and dot-directories such as `.git`,
     `.DS_Store`).
  6. For each survivor, compute `ext = p.suffix.lower()`, run
     `classify(p)`, and append a `LocalFile`.
  Returns the list, possibly empty. Pure-read I/O only; never writes.

**Internal dependencies**

- None.

**External dependencies**

- `pathlib` (stdlib), `dataclasses` (stdlib).

**Invariants**

- The result is deterministically ordered (alphabetical by absolute
  path, courtesy of `sorted(rglob(...))`).
- Hidden files and hidden directories are excluded *recursively*: a
  file under `.git/` is skipped because one of its relative-path
  components starts with `.`.
- `kind` is always one of the six documented values; never `None` or
  empty.
- `ext` is always lowercased and includes the leading `.`, or is the
  empty string for extensionless files (which classify to `"skip"`).

**Error modes**

- `ValueError` if `root` is not a directory.
- `OSError` from `rglob` on permission errors or vanished entries
  during iteration; not caught here.

**Notes**

- Classification is *purely by extension*. A `.pdf` that is actually
  HTML will still be sent to the OCR backend, which will likely fail
  and produce a `status="failed"` entry. Magic-byte sniffing is
  intentionally out of scope.
- The hidden-component check uses `relative_to(root).parts`, so a
  visible file under a visible root whose absolute path happens to
  traverse `/home/user/.config/...` outside `root` is *not* affected.
  Only components *under* `root` matter.

#### 4.7.5 `app/services/textextract.py`

- **Path**: `app/services/textextract.py`
- **Purpose**: Convert text-native file kinds (docx, xlsx, csv, text)
  to a markdown body string. Fully local; no model invocation.

**Public interface**

- `extract_docx(path: Path) -> str`
  Opens the file with `python-docx` (`docx.Document(str(path))`).
  Walks `doc.paragraphs` first, appending each stripped, non-empty
  paragraph text to a parts list. Then walks `doc.tables`; for each
  row, joins the stripped cell texts with `" | "` and appends as one
  line. Joins all parts with `"\n\n"` (blank-line-separated). Returns
  the result. Raises whatever `python-docx` raises for unreadable or
  corrupt files (typically `PackageNotFoundError`).

- `extract_xlsx(path: Path) -> str`
  Opens the workbook with `openpyxl.load_workbook(str(path),
  data_only=True, read_only=True)`. `data_only=True` substitutes
  cached formula results for the formulas themselves; `read_only=True`
  streams for low memory. For each worksheet, iterates rows
  (`values_only=True`), stringifies cells (`None` -> `""`), drops rows
  whose stripped concatenation is empty, joins cells with `" | "`, and
  joins surviving rows with `"\n"`. Each non-empty sheet's block is
  prefixed with a `## Sheet: <ws.title>` heading. Blocks are joined
  with `"\n\n"`. Returns the result. Raises whatever `openpyxl`
  raises for unreadable files.

- `extract_csv(path: Path) -> str`
  Opens the file with UTF-8 + `errors="replace"` (so undecodable bytes
  become U+FFFD rather than aborting) and `newline=""` (per
  `csv.reader` contract). Uses `csv.reader` with default dialect.
  Joins each row's cells with `" | "` and joins rows with `"\n"`.
  Returns the result.

- `extract_text(path: Path) -> str`
  Reads the file as UTF-8 with `errors="replace"` and returns the
  raw text. No transformation. Used for both `.txt` and already-
  markdown files (`.md`, `.markdown`).

**Internal dependencies**

- None.

**External dependencies**

- `python-docx` (`docx.Document`) — imported inside `extract_docx`.
- `openpyxl` (`load_workbook`) — imported inside `extract_xlsx`.
- `csv`, `io`, `pathlib` (stdlib).

**Invariants**

- Each function returns a `str`. Empty input files yield `""`.
- No function writes to the filesystem.
- No function performs network I/O.
- Cell separator across all tabular extractors is the literal
  `" | "` (space, pipe, space). This is *not* GFM-compliant markdown
  table syntax; downstream tooling treats it as a pipe-delimited line,
  not a rendered table.

**Error modes**

- `extract_docx`: `docx.opc.exceptions.PackageNotFoundError` for
  corrupt or non-docx files; `KeyError` / `AttributeError` from
  unusual document structures.
- `extract_xlsx`: `openpyxl` raises various `InvalidFileException` /
  `BadZipFile` for corrupt files; `KeyError` for missing relationships.
- `extract_csv`: `OSError` / `UnicodeDecodeError` are largely avoided
  by `errors="replace"`, but malformed quoting can raise
  `csv.Error`.
- `extract_text`: `OSError` on unreadable files.

All such exceptions propagate; the caller in `main.py` records them
as `FileResult(status="failed", reason=str(e))`.

**Notes**

- DOCX heading levels are *flattened*: `add_heading("Foo", level=2)`
  emits a paragraph whose text is `"Foo"`, with no leading `##`. The
  level metadata is lost. This is a known limitation; downstream
  consumers must not rely on markdown heading hierarchy from DOCX
  inputs.
- XLSX numeric formatting is lost: `data_only=True` returns the
  cached value as a Python `float` / `int`, and the extractor
  `str()`s it. A cell formatted as `$1,180.00` will appear as
  `1180` or `1180.0`. Acceptable for content extraction; not
  acceptable for faithful spreadsheet reproduction.
- The CSV extractor uses the default `csv` dialect (comma separator,
  `"` quote char). Files with tab or semicolon separators will be
  read as single-column rows. Out of scope to auto-detect.
- The text extractor passes through markdown content as-is; it does
  not re-render or canonicalize.

#### 4.7.6 `app/services/postprocess.py`

- **Path**: `app/services/postprocess.py`
- **Purpose**: Mistral-OCR-specific output normalization: drop inline
  image references, inline HTML tables stored under stable `tbl-N.html`
  ids, and wrap each page with explicit markers.

**Public interface**

- `inline_tables_and_drop_images(text: str, tables) -> str`
  Builds a map `{t.id: t.content for t in tables or []}`. Mistral's
  OCR markdown references tables as a markdown-link form
  `[tbl-N.html](tbl-N.html)`; this function substitutes the literal
  HTML content for each such reference using
  `re.sub(r"\[(tbl-\d+\.html)\]\(\1\)", repl, text)`. When the id is
  unknown, the original match is preserved. Then it strips
  whole-line image references with `re.sub(r"(?m)^\s*!\[[^\]]*\]\([^)]+\)\s*\n?", "", text)`.
  Returns the stripped string.

- `to_wrapped_markdown(res) -> str`
  Accepts a Mistral OCR response object. For each page `p` in
  `res.pages`, calls
  `inline_tables_and_drop_images(p.markdown, p.tables)`, then wraps
  the resulting body in:
  ```
  [[START OF PAGE {p.index + 1}]]

  {body}

  [[END OF PAGE {p.index + 1}]]
  ```
  Joins all wrapped pages with `"\n\n"` and returns the result. The
  `+1` converts Mistral's 0-based page index to a 1-based marker for
  human readability.

**Internal dependencies**

- None.

**External dependencies**

- `re` (stdlib).

**Invariants**

- Page markers use double-bracket delimiters `[[ ... ]]` exactly as
  shown; downstream parsers (e.g. graphify) match this literal.
- Page numbers in markers are 1-based.
- Image references stripped are the whole-line form only: the regex
  is anchored with `^\s*` and trailing `\s*\n?`. Inline images
  embedded mid-paragraph would survive; in practice Mistral OCR
  emits each image on its own line.
- Table ids match `tbl-\d+\.html` exactly. Any other table-link form
  is left untouched.

**Error modes**

- `AttributeError` if `res` is not a Mistral OCR response (missing
  `.pages`); never caught here.

**Notes**

- This module is Mistral-specific. Docling output already arrives as
  a single flattened markdown document and does not need wrapping.
  Generalizing this module to non-Mistral backends is out of scope.
- The replacement function preserves unknown table ids
  (`table_map.get(m.group(1), m.group(0))`), so a partial table set
  produces best-effort output rather than corruption.

#### 4.7.7 `app/services/mistral.py`

- **Path**: `app/services/mistral.py`
- **Purpose**: OCR backend that uploads each file to the Mistral
  Files API, runs `mistral-ocr-latest`, and returns
  page-wrapped markdown via `postprocess.to_wrapped_markdown`.

**Public interface**

- `class MistralService`
  - `__init__(self, api_key: str)`
    Constructs a `mistralai.Mistral(api_key=api_key)` client and
    stores it on `self.client`. No network call yet.
  - `convert(self, path: Path) -> str`
    Reads `path` as bytes, uploads via
    `self.client.files.upload(file={"file_name": path.name,
    "content": data}, purpose="ocr")`, then calls
    `self.client.ocr.process(model="mistral-ocr-latest",
    document={"type": "file", "file_id": uploaded.id},
    table_format="html")`. Returns
    `to_wrapped_markdown(res)`. The class satisfies the implicit OCR
    backend protocol (section 4.3).

**Internal dependencies**

- `app.services.postprocess` (`to_wrapped_markdown`)

**External dependencies**

- `mistralai` (`Mistral`)
- `pathlib` (stdlib)

**Invariants**

- The OCR model is pinned to `"mistral-ocr-latest"` at v0.3.0. A future
  version MAY parameterize this via `settings`; do not hard-code a
  rolling alias if reproducibility matters.
- `table_format="html"` is required: `to_wrapped_markdown` /
  `inline_tables_and_drop_images` expect HTML payloads under
  `tbl-N.html` ids. Changing the table format without also updating
  postprocess will break table inlining.
- One upload per `convert` call. There is no batching, no caching of
  uploaded `file_id`s, and no explicit cleanup of the uploaded file.
  Files remain on Mistral's side until the account's retention policy
  evicts them.

**Error modes**

- Anything raised by `mistralai` (network errors, auth errors,
  rate limits, model errors, oversized files) propagates. The
  per-file `try/except` in `process_folder` converts each into a
  `FileResult(status="failed", reason=str(e))`.
- `OSError` from `path.read_bytes()` propagates similarly.

**Notes**

- This is the only code path that touches the network in the entire
  application.
- Per-file latency is dominated by upload + model time; expect tens of
  seconds per page for typical SBC-style PDFs.
- `mistralai` is imported at module top, so importing
  `app.services.mistral` is the trigger for loading the SDK. The
  factory in `main.py` only imports this module when
  `OCR_BACKEND=mistral`, preserving lazy-import discipline at the
  process level.

#### 4.7.8 `app/services/docling_ocr.py`

- **Path**: `app/services/docling_ocr.py`
- **Purpose**: Fully-local OCR / document-conversion backend wrapping
  IBM's Docling library.

**Public interface**

- `class DoclingService`
  - `__init__(self)`
    Inside the constructor body, imports
    `from docling.document_converter import DocumentConverter`, then
    constructs and stores `self._converter = DocumentConverter()`. The
    import is intentionally deferred; see section 4.4.
  - `convert(self, path: Path) -> str`
    Calls `result = self._converter.convert(str(path))` and returns
    `result.document.export_to_markdown()`. The class satisfies the
    implicit OCR backend protocol (section 4.3).

**Internal dependencies**

- None.

**External dependencies**

- `docling` (`docling.document_converter.DocumentConverter`),
  imported lazily inside `__init__`.
- `pathlib` (stdlib).

**Invariants**

- Docling's `DocumentConverter` is constructed exactly once per
  process; it is reused across all `convert` calls. It is the
  expensive object (model load, tokenizer load).
- All work is local. No outbound network calls. (Docling MAY
  download models on first use; that is a one-time install-time
  concern, not a per-request network dependency.)
- The output is a single flat markdown document with no
  `[[START OF PAGE n]]` markers. This is intentional: page markers
  are a Mistral-specific affordance because Mistral's OCR is
  page-structured, while Docling's output preserves document
  structure differently.

**Error modes**

- `docling` may raise on unsupported formats, malformed PDFs, or
  model failures. All such exceptions propagate to the per-file
  `try/except` in `process_folder`.
- `ImportError` at construction time if `docling` is not installed.
  This will only surface in a process whose `OCR_BACKEND=docling`;
  Mistral-only deployments need not install `docling`.

**Notes**

- Docling output has *no page markers*. Downstream tooling that
  expects `[[START OF PAGE n]]` must conditionally handle the
  Docling case (e.g. treat the whole document as page 1).
- Docling supports far more input formats than this service exposes;
  the classifier in `local_fs.py` restricts the OCR kind to the
  documented extensions (`.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp`,
  `.tiff`, `.tif`). Expanding the set is a `local_fs` change, not a
  backend change.
- For deterministic CPU-only runs in CI, Docling's model selection
  may need tuning; that is configuration the service does not
  expose at v0.3.0.

### 4.8 Cross-module contracts (summary)

The non-obvious cross-module contracts that a reimplementation MUST
preserve:

- The OCR backend's `convert` returns final markdown. The caller does
  *not* post-process it. Therefore, vendor-specific wrapping belongs
  *inside* the backend (Mistral does this via `postprocess`; Docling
  does not need it).
- `local_fs.classify` is the *only* authority for `kind`. The dispatch
  in `main.py` must cover every value `classify` can return.
- `_make_ocr_backend()` is the *only* place that names concrete backend
  classes. The rest of the codebase sees only the duck-typed
  `ocr.convert(path)`.
- Failure isolation lives at the per-file granularity in `main.py`, not
  inside the services. Service modules are free to raise; the loop
  catches.
- The output path is always `out_root / rel.with_suffix(".md")`. Even
  inputs that are already markdown (`.md`, `.markdown`) are rewritten
  under `out_root` so the output tree is self-contained.

### 4.8 Upload pipeline modules

The browser-facing surface adds three modules and refactors one. All conversion logic stays in the original service modules; the new modules are concerned with file delivery, job state, and packaging.

#### 4.8.1 `app/services/runner.py`

- **Purpose**: shared per-file batch loop. Originally inlined in `app/main.py:process_folder`; extracted so both `/convert` and the upload job runner call the same implementation.
- **Public interface**:
  - `process_directory(in_root: Path, out_root: Path, ocr, *, on_progress: Callable[[FileResult], None] | None = None) -> list[FileResult]`
- **Contract**: walks `in_root` via `local_fs.walk`, dispatches per `kind` exactly as before, writes `.md` mirrors under `out_root`, returns the `FileResult` list in walk order. The optional `on_progress` callback fires after every result append; the upload runner uses this to surface live progress in the polling endpoint.
- **Invariants**: failure isolation per file (same `try/except Exception` discipline as the previous inline loop); empty corpora return an empty list without raising.
- **Internal deps**: `app.services.local_fs.walk`, `app.services.textextract.*`, `app.models.FileResult`.

#### 4.8.2 `app/services/jobs.py`

- **Purpose**: in-memory job state plus background-task runner for the upload pipeline.
- **Public interface**:
  - `Job` dataclass with fields `id`, `created_at`, `updated_at`, `status` (`Literal["queued","running","completed","failed"]`), `input_dir`, `output_dir`, `zip_path`, `results: list[FileResult]`, `error: str | None`.
  - `JobStore(data_dir: Path, ttl_seconds: int)` — process-local singleton; constructor takes the root data directory and a TTL.
  - `JobStore.create() -> Job` — mints a UUID, prepares `data_dir/jobs/<id>/{in,out}/`, registers a `Job` in `status="queued"`.
  - `JobStore.get(job_id: str) -> Job | None`.
  - `JobStore.write_upload(job: Job, filename: str, data: bytes) -> Path` — sanitizes filename, asserts path-traversal safety, writes the bytes, disambiguates collisions.
  - `JobStore.run(job_id: str, ocr) -> None` — synchronous batch runner intended to execute inside a FastAPI `BackgroundTask`; updates the `Job` status and results in place; on completion produces `out.zip` next to `out/`.
  - `JobStore.sweep() -> int` — deletes terminal-state jobs older than the TTL (both in-memory entry and on-disk directory tree). Returns count removed.
  - `_safe_filename(name: str) -> str` — private helper documenting the sanitization rules: strip directory separators, drop leading dots, remove ASCII control characters, cap at 200 bytes.
- **Concurrency model**: single `threading.Lock` around the `_jobs` dict. The lifespan handler spawns one daemon `_sweeper_loop` thread; nothing else mutates job state concurrently in the current scope.
- **Filesystem layout per job**: `${DATA_DIR}/jobs/<job_id>/in/` for uploads, `${DATA_DIR}/jobs/<job_id>/out/` for `.md` outputs, `${DATA_DIR}/jobs/<job_id>/out.zip` for the download artifact.
- **Internal deps**: `app.services.runner.process_directory`, `app.services.zip_out.zip_directory`, `app.models.FileResult`.

#### 4.8.3 `app/services/zip_out.py`

- **Purpose**: single-shot directory-to-zip writer.
- **Public interface**: `zip_directory(src: Path, dest: Path) -> Path`. Recursively zips every file under `src` (preserving `src`-relative arcnames), DEFLATE compression. Raises `ValueError` if `src` is not a directory.
- **Notes**: writes to disk (not a streaming generator) because the file count per job is capped at `MAX_FILES_PER_JOB` and total bytes are bounded by `MAX_UPLOAD_BYTES`; an on-disk artifact also serves the `FileResponse` download cleanly.

### 4.9 Updated component diagram

```text
            ┌────────────────────────────────────────────────────────┐
Browser ──► │ FastAPI app (uvicorn)                                  │
            │                                                        │
            │  GET  /            ─► Jinja2: index.html               │
            │  GET  /static/*    ─► StaticFiles                      │
            │  GET  /health      ─► liveness                         │
            │  POST /convert     ─► process_directory ─► runner      │
            │  POST /upload      ─► JobStore.create + write_upload   │
            │                       └─ BackgroundTask: JobStore.run  │
            │                            └─► process_directory       │
            │                            └─► zip_directory           │
            │  GET  /jobs/{id}   ─► JobStore.get                     │
            │  GET  /jobs/{id}/download ─► FileResponse(zip)         │
            │                                                        │
            │  lifespan: spawn daemon thread → JobStore.sweep()      │
            └────────────────────────────────────────────────────────┘
                              │
                              ▼ (shared)
   ┌───────────────────────────────────────────────────────────────┐
   │ services.runner.process_directory(in, out, ocr, on_progress)  │
   │   │                                                           │
   │   ├─ services.local_fs.walk + classify                        │
   │   └─ per kind:                                                │
   │      ocr   → ocr.convert(path)        (Docling | Mistral)     │
   │      docx  → textextract.extract_docx                         │
   │      xlsx  → textextract.extract_xlsx                         │
   │      csv   → textextract.extract_csv                          │
   │      text  → textextract.extract_text                         │
   │      skip  → FileResult(skipped)                              │
   └───────────────────────────────────────────────────────────────┘
```

### Files of record

```text
app/main.py                     orchestrator, factory, FastAPI app, upload routes
app/config.py                   Settings (OCR_BACKEND, MISTRAL_API_KEY, upload knobs)
app/models.py                   ProcessFolderResponse, FileResult, JobSubmit/Status
app/services/local_fs.py        walk + classify, LocalFile dataclass
app/services/textextract.py     extract_docx / xlsx / csv / text
app/services/postprocess.py     Mistral page-wrap + table inlining
app/services/mistral.py         MistralService (network backend)
app/services/docling_ocr.py     DoclingService (local backend)
app/services/runner.py          process_directory (shared batch loop)
app/services/jobs.py            Job, JobStore, _safe_filename, sweeper
app/services/zip_out.py         zip_directory
app/templates/index.html        upload UI
app/static/app.js               upload + poll
app/static/style.css            upload UI styles
```

---

## 5. Setup, environment, and runtime

This section is normative: a recreation of the project must satisfy every requirement listed here. Variables, defaults, and shell commands are reproduced verbatim and should not be paraphrased in the rebuilt project.

### 5.1 Supported Python interpreter

- **Required:** CPython **3.11.x** (any patch release of the 3.11 line).
- **Verified runtime:** the project is developed and smoke-tested against Python 3.11 on Linux x86_64.
- **Why 3.11 specifically:** the heaviest transitive dependencies of `docling` are `torch` and the rest of the PyTorch CPU stack, plus `transformers` and the OCR engines (`easyocr` / `rapidocr`). At the time this spec was authored, the PyTorch project does not publish manylinux wheels for Python 3.13 or 3.14, which causes `pip install -r requirements.txt` to either fall through to a source build (slow, often failing) or fail outright. Python 3.11 has full wheel coverage across every transitive dependency.
- **Likely-but-untested:** Python **3.10** is expected to work — none of the pinned packages declare a `>=3.11` floor — but the project has not been smoke-tested on 3.10 and the spec does not warrant it. Python **3.12** should also work in principle; the PyTorch CPU wheels are published for 3.12 and the rest of the stack supports it, but again the project has not exercised that path.
- **Not supported:** Python 3.13 and 3.14 are out of scope until upstream wheels are available.

If a future maintainer wishes to bump the interpreter floor, the verification checklist is: (a) `pip install -r requirements.txt` resolves without falling back to source builds, (b) `python -c "from docling.document_converter import DocumentConverter; DocumentConverter()"` succeeds, and (c) the smoke test in section 6.2 passes end-to-end.

### 5.2 Operating system

- **Verified:** Linux x86_64 (developed on Arch Linux, kernel 6.x).
- **Should work, not smoke-tested:** macOS (both Intel and Apple Silicon) and Windows 10/11.
  - On macOS, `torch` wheels are available for Python 3.11; Docling has been reported to run on Apple Silicon via the CPU path.
  - On Windows, `python-multipart`, `fastapi`, and `uvicorn` are all platform-agnostic; the only friction historically has been long path support for the Docling weight cache. Use a recent Windows release with long paths enabled.
- **Containerization:** the project is not currently published as a Docker image, but every dependency is `pip`-installable in a stock `python:3.11-slim` base. A future Dockerfile is a reasonable but out-of-scope addition.

### 5.3 Hardware expectations

- **CPU:** a modern laptop CPU (any x86_64 chip from the last ~5 years) is sufficient. No special instruction-set requirements beyond what stock PyTorch CPU wheels target (AVX2 is helpful but not required).
- **GPU:** **not required** for the default `OCR_BACKEND=docling` configuration. Docling will use a CUDA device if one is present and PyTorch was installed with CUDA wheels, but the pinned `requirements.txt` resolves to CPU-only PyTorch by default and the verified path is CPU-only.
- **Memory:** **~4 GB free RAM** is comfortable while processing the bundled test fixtures. Larger PDFs or longer batches can push the resident set higher; Docling's layout and OCR models together occupy several hundred MB once loaded.
- **Disk budget:** plan for approximately **3 GB** of disk for the virtual environment. Breakdown (approximate):
  - PyTorch CPU wheel: ~750 MB to 1 GB depending on platform.
  - Docling + its DocumentConverter dependencies (datasets, models registry): a few hundred MB.
  - `transformers` and tokenizer wheels: ~200 MB.
  - EasyOCR / RapidOCR vendored model bundles (downloaded lazily on first OCR run): a few hundred MB.
  - FastAPI, Uvicorn, Pydantic, `python-docx`, `openpyxl`, `python-multipart`, `python-dotenv`, `mistralai` and their dependencies: well under 100 MB combined.
- **First-run model download:** the very first PDF conversion in a process triggers Docling to download layout and OCR model weights to `~/.cache/docling` (or the platform equivalent). Total download is a few hundred MB. Subsequent runs reuse the cache. If your environment is offline you must pre-populate this cache; the spec does not describe an offline-install path because the verified flow assumes internet access on first run.
- **Where the model-download trigger lives in the code:** `app/services/docling_ocr.py` defines `DoclingService.__init__` to instantiate a `docling.document_converter.DocumentConverter` with no arguments. That constructor lazily wires up the model registry; the actual weight download is deferred until the first call to `self._converter.convert(...)`. The implication for the spec: simply *importing* `docling_ocr` or constructing `DoclingService` does not exercise the network. The first `/convert` request that includes a PDF (or other OCR-routed extension) is what pays the download cost. A useful pre-warm command for cold deployments is shown in section 6.6 (the manual extractor verification block) — running the Docling one-liner against any small PDF will trigger the download once, after which the server's first request will hit a warm cache.
- **Cache directory location:** by default `~/.cache/docling` on Linux. macOS may use `~/Library/Caches/docling` depending on the Docling version. The cache is safe to delete; deletion forces a fresh download on the next invocation. The cache is also safe to copy between machines with matching architectures (the weights themselves are platform-agnostic; PyTorch loads them on whichever device is configured).

### 5.4 Virtual environment recipe

The project assumes an isolated virtual environment created with the stdlib `venv` module. Exact shell commands, in order:

```bash
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

These three commands are sufficient to bring up the runtime. The third command performs the heavy work and may take several minutes on a fresh machine because it pulls PyTorch CPU wheels and Docling's dependency tree.

For the developer-only test-fixture generator (`scripts/gen_test_data.py`), one additional package is required:

```bash
.venv/bin/pip install reportlab
```

**`reportlab` is intentionally not a runtime dependency.** It is not listed in `requirements.txt`. It is needed only because `gen_test_data.py` synthesizes a small PDF (`sbc_excerpt.pdf`) to exercise the OCR path. A production deployment of the converter does not need `reportlab` installed. A recreation that omits the test-fixture generator may omit `reportlab` entirely.

After installation, sanity-check the environment with:

```bash
.venv/bin/python -c "import fastapi, uvicorn, docling, docx, openpyxl, mistralai; print('ok')"
```

#### 5.4.1 Common installation failure modes

These are not bugs in the project; they are environment issues a recreator is likely to hit on first install. They are listed here so the spec is self-contained.

- **`python3.11: command not found`.** The system does not have CPython 3.11. Install via the platform's package manager (`pacman -S python311` on Arch, `apt install python3.11` on Debian/Ubuntu with the deadsnakes PPA, `brew install python@3.11` on macOS) or via `pyenv`. Do **not** substitute `python3` blindly — on systems where `python3` is 3.13 the install will succeed up to the PyTorch step and then fail.
- **PyTorch wheel resolution stalls on "Collecting torch".** This is the heaviest single download in the dependency tree. On a slow network, expect several minutes. If `pip` falls back to a source build (visible as `Building wheel for torch`), abort and verify that the interpreter is 3.11 — source builds for `torch` are not supported by this spec.
- **`docling` import succeeds but `DocumentConverter()` hangs.** First construction downloads model weights from the Hugging Face hub. If the network is firewalled, this hangs indefinitely (the underlying HTTP client has no aggressive timeout). Solutions: (a) allow outbound HTTPS to `huggingface.co`, or (b) pre-populate `~/.cache/docling` from a machine that has internet access and copy the cache directory across.
- **`pip` itself is too old.** The `pip install --upgrade pip` step in section 5.4 is not cosmetic; modern resolver behavior is required to pick the correct PyTorch wheel. If you skip it on an old base image, you may see resolver errors.
- **Disk full during install.** The PyTorch CPU wheel alone is roughly 750 MB to 1 GB. Combined with Docling's transitives, the install consumes ~3 GB. `pip` does not pre-flight free space; an out-of-space failure shows up as a partial install with cryptic tracebacks. Free disk before retrying.

### 5.5 Environment variables

The runtime reads exactly two environment variables. Both are typed and validated by `pydantic-settings` via the `Settings` model in `app/config.py`. Unknown variables are ignored (`extra="ignore"`).

| Name              | Type   | Default    | Required when             | Semantics                                                                                                                                                                                          |
|-------------------|--------|------------|---------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `OCR_BACKEND`     | string | `docling`  | Only to override default  | Selects the OCR engine for PDFs and image-like inputs. Allowed values: `docling` (local CPU inference via Docling) or `mistral` (hosted inference via the Mistral OCR API). Case-insensitive at read time — `app/main.py` lowercases the value before dispatching. Any other value raises `RuntimeError` at startup. |
| `MISTRAL_API_KEY` | string | `""` (empty) | When `OCR_BACKEND=mistral` | Bearer token for the Mistral OCR API. If `OCR_BACKEND=docling`, this variable is ignored and need not be set. If `OCR_BACKEND=mistral` and the value is empty, application startup raises `RuntimeError("OCR_BACKEND=mistral but MISTRAL_API_KEY is empty")`. |

There are no other environment knobs. Host, port, worker count, input directory, and output directory are all passed at the call site (Uvicorn CLI flags and the `/convert` form fields respectively), not via environment.

#### 5.5.1 Edge cases the settings loader handles

- **Missing `.env` file:** not an error. `Settings()` falls back to the class-level defaults (`OCR_BACKEND="docling"`, `MISTRAL_API_KEY=""`). The default configuration therefore works on a fresh checkout with no setup beyond installing dependencies.
- **`.env` present but variable absent:** the missing variable takes its default. `MISTRAL_API_KEY` being unset is fine as long as `OCR_BACKEND` is `docling`.
- **Extra variables in `.env`:** ignored (`extra="ignore"` in `SettingsConfigDict`). Adding comments or unrelated keys to `.env` will not crash the loader.
- **Case sensitivity of `OCR_BACKEND` values:** the variable itself is read as written, but `app/main.py` calls `.lower()` on it before dispatching, so `OCR_BACKEND=Docling`, `OCR_BACKEND=DOCLING`, and `OCR_BACKEND=docling` all behave identically. Unknown values (e.g. `OCR_BACKEND=tesseract`) raise `RuntimeError(f"Unknown OCR_BACKEND: {backend}")` at startup.
- **Whitespace in `MISTRAL_API_KEY`:** `pydantic-settings` does not strip the value. A trailing newline or space in `.env` becomes part of the key and will produce 401s from the Mistral API. The spec does not mandate stripping; recreations should keep `.env` clean.

### 5.6 `.env` loading

`app/main.py` calls `dotenv.load_dotenv()` as its very first executable statement, before any import that touches `app.config`. This means a `.env` file in the **current working directory at the time `uvicorn` is launched** will be loaded into `os.environ` before `Settings()` is instantiated. The canonical template is checked in at `.env.example` and contains:

```
OCR_BACKEND=docling
MISTRAL_API_KEY=
```

To activate it, copy and edit:

```bash
cp .env.example .env
# then edit .env to set MISTRAL_API_KEY if you intend to use OCR_BACKEND=mistral
```

`.env` itself is gitignored. `.env.example` is committed. Pydantic's `SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")` provides a second, redundant load path; the explicit `load_dotenv()` in `app/main.py` is what guarantees correct behavior when other modules read `os.environ` directly during import.

### 5.7 Starting the server

The canonical invocation is:

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

#### 5.7.1 Port

The choice of TCP port is free. Any unused port is acceptable. `8000` is the de facto default for Uvicorn and is used throughout this spec's examples. If `8000` is occupied, pick anything else (e.g. `8080`, `5050`) and substitute it into every example.

#### 5.7.2 Host binding

- `--host 127.0.0.1` binds to localhost only. **This is the recommended default** for local development and the configuration this spec assumes elsewhere.
- `--host 0.0.0.0` binds to every interface and exposes the service on the LAN. **This is not recommended** because the application implements **no authentication, no authorization, and no rate limiting**. The `/convert` endpoint takes filesystem paths as form input and writes to filesystem paths under the server's privileges; an exposed instance is an arbitrary-write-on-host primitive for any LAN attacker. If LAN exposure is required, the deployment must front the service with an authenticating reverse proxy.

#### 5.7.3 Workers

Workers default to `1`. The OCR backend instance is constructed at module-import time (see `ocr = _make_ocr_backend()` at the top of `app/main.py`), which means **each worker process owns its own copy of the Docling models in memory**. Multiplying workers multiplies RAM usage by approximately the same factor. Raise `--workers N` only if both of the following are true: (a) you have measured that batches are CPU-bound rather than I/O-bound, and (b) you have RAM headroom for N independent Docling instances.

#### 5.7.4 Reload / dev mode

`uvicorn --reload` works but is not part of the spec's verified configuration. It is fine for local development. Do not enable it in any setting where the process restart cost (which includes reloading Docling models) matters.

### 5.7.5 Filesystem semantics at the call site

The `/convert` endpoint takes two form fields: `input_dir` (required) and `output_dir` (optional, default `./out`). Both are interpreted by the **server process**, not the client:

- Paths are passed through `Path(...).expanduser().resolve()`. `~` is expanded against the server's `HOME`; relative paths resolve against the server's current working directory.
- If `input_dir` is not an existing directory after resolution, the server returns `HTTP 400` with body `{"detail": "Not a directory: <resolved-path>"}`.
- If `output_dir` does not exist, the server creates it (including parents) via `mkdir(parents=True, exist_ok=True)`. The server must have write permission on the parent.
- Output files mirror the input tree: a file at `<input_dir>/a/b/c.docx` becomes `<output_dir>/a/b/c.md`. Intermediate directories under `output_dir` are created on demand.
- The walker (`app/services/local_fs.py:walk`) recurses with `rglob("*")` and skips any path whose relative components include a dot-prefixed segment. Dotfiles and dot-directories are invisible to the converter by design — do not rely on them being processed.
- File classification is by **extension only** (case-folded). The walker does not sniff content. A `.pdf` with non-PDF bytes will be sent to the OCR backend and will fail there, not at classification time.
- Recognized extensions (from `local_fs.py`): `.pdf .png .jpg .jpeg .webp .tiff .tif` (OCR), `.docx` (DOCX), `.xlsx .xlsm` (XLSX), `.csv` (CSV), `.txt .md .markdown` (text). Anything else yields `kind="skip"` and a `status="skipped"` `FileResult` with `reason="Unsupported extension: <ext>"`.

### 5.7.6 Selecting the Mistral OCR backend

The default `OCR_BACKEND=docling` is the recommended configuration and the only one this spec verifies end-to-end. The `mistral` backend is supported but has a different operational profile:

- **API key:** obtain a key from the Mistral console and place it in `.env` as `MISTRAL_API_KEY=<key>`. Set `OCR_BACKEND=mistral` in the same file.
- **Startup behavior:** `_make_ocr_backend()` constructs a `MistralService` (see `app/services/mistral.py`). This wraps the `Mistral` client from the `mistralai` SDK and stores it on `self.client`. No network call is made at construction time.
- **Per-request behavior:** each PDF triggers two network calls — `files.upload(purpose="ocr")` then `ocr.process(model="mistral-ocr-latest", document={"type": "file", "file_id": <id>}, table_format="html")`. The output is wrapped page-by-page with `[[START OF PAGE N]] ... [[END OF PAGE N]]` markers by `app/services/postprocess.py:to_wrapped_markdown`.
- **Cost:** every page sent to `/convert` is a billable API call. Do not run the smoke test against this backend casually.
- **Latency:** dominated by network round-trip and Mistral's server-side processing. Typically 1–3 seconds per page; no cold start on the client.
- **No model cache:** `~/.cache/docling` is not used. The disk footprint of a Mistral-only install is dramatically smaller (no PyTorch, no Docling models), but `docling` is still in `requirements.txt` and will be installed because the file does not split into extras. A recreator who wants a Mistral-only install can remove the `docling>=2.0.0` line from `requirements.txt` and remove `app/services/docling_ocr.py`; the conditional import in `_make_ocr_backend()` means Docling is never touched at runtime when `OCR_BACKEND=mistral`. The spec does not mandate this slimming, but it is supported.
- **Failure modes:** an invalid or empty `MISTRAL_API_KEY` raises `RuntimeError` at startup (server fails to bind). Network errors during `/convert` surface as `status="failed"` entries in the response with the SDK's exception message in `reason`.

### 5.8 Logging

The application uses **`print()` statements only**. There is no `logging` configuration, no structured logger, no log rotation, no JSON output, and no log level control. The only logging point in the runtime is in `app/main.py`:

```python
print(f"FILES FOUND: {len(files)} · OCR_BACKEND={settings.OCR_BACKEND}")
```

This is intentional for the project's current scope (a single-user local utility). A production deployment would replace `print()` with the stdlib `logging` module configured against `uvicorn`'s log handlers, but that work is explicitly out of scope. Treat the `print()` output as informational only; do not parse it as a log stream.

Uvicorn's own access log (one line per HTTP request) is enabled by default and is independent of the application's `print()` calls. Disable it with `--no-access-log` if it is noisy.

### 5.9 Process lifecycle and shutdown

The application has no startup or shutdown hooks beyond the implicit ones FastAPI provides. The OCR backend is constructed at module import time and held in a module-level global (`ocr` in `app/main.py`). Specifically:

- **Startup:** `import app.main` triggers `load_dotenv()`, then `Settings()` reads the environment, then `_make_ocr_backend()` constructs `DoclingService` or `MistralService`. Once that returns, Uvicorn begins accepting requests. There is no `@app.on_event("startup")` handler.
- **Shutdown:** sending `SIGINT` (Ctrl+C) or `SIGTERM` to the Uvicorn process terminates immediately. No graceful drain of in-flight `/convert` requests. There is no `@app.on_event("shutdown")` handler and no cleanup of partial output files. A request interrupted mid-way through `/convert` leaves whatever it wrote on disk; rerunning the request overwrites those files.
- **No persistent state across restarts** other than the on-disk Docling model cache (`~/.cache/docling`) and any output files already written. The server itself is stateless between requests.
- **Concurrency model:** Uvicorn with workers=1 serves requests serially within a single event loop. The `/convert` handler is a synchronous function (`def`, not `async def`), so FastAPI runs it on a thread pool — meaning two concurrent `/convert` calls can in principle overlap, but they share the single `ocr` backend instance. Neither `DoclingService` nor `MistralService` documents thread safety. For now, treat `/convert` as effectively single-flight. If concurrent batches are needed, use `--workers N` (mind the RAM cost discussed in 5.7.3) rather than concurrent requests against a single worker.

### 5.10 Deployment posture (local operator)

The verified single-host deployment mode is **one user, one machine, one Uvicorn process bound to localhost, invoked manually**. Recreations targeting an operator workflow should match this posture unless they explicitly extend it.

The trust boundary is the host. `/convert` takes filesystem paths and writes to filesystem paths under the server process's UID. Anyone who can reach the bound socket can read any file readable by that UID (by passing a path as `input_dir`) and write to any directory writable by that UID (by passing a path as `output_dir`). There is no authentication, no allowlist of paths, no chroot, and no sandboxing. Keeping the bind on `127.0.0.1` is the spec's primary mitigation. Any extension of this deployment to a network-reachable surface must add authentication; the spec does not specify how.

### 5.11 Web deployment (Render + Docker)

A second supported deployment mode targets a public web demo running on Render's container service.

#### Container layout

A multi-stage `Dockerfile` lives at the repo root. Stage 1 (`python:3.11-slim` + `build-essential`) builds wheels for every entry in `requirements.txt`. Stage 2 is a minimal `python:3.11-slim` plus the runtime system libraries Docling requires (`libgl1`, `libglib2.0-0`), installs the wheels from stage 1, copies `app/` only (excluding `bench/`, `test_data/`, `out/`, `SPEC.md`, etc., per `.dockerignore`), runs as a non-root user (`app`, UID 1000), exposes port 8000, and starts uvicorn with `--host 0.0.0.0 --port 8000`. The image declares a `HEALTHCHECK` that issues `GET /health` every 30 s.

| Layer concern | Spec |
|---|---|
| Base | `python:3.11-slim` (both stages) |
| Build deps | `build-essential` (stage 1 only) |
| Runtime deps | `libgl1`, `libglib2.0-0` (for Docling's image and OpenCV-dependent paths) |
| App user | `app` (UID 1000), non-root |
| Workdir | `/home/app` |
| Data dir | `/data` (chown to `app`) at image build time; mounted from a persistent disk in production |
| Docling cache | `/home/app/.cache/docling`; mounted from a persistent disk in production |
| Command | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |

#### `render.yaml`

The project ships a `render.yaml` Infrastructure-as-Code file consumed by Render's blueprint workflow. The verified contents are:

```yaml
services:
  - type: web
    name: bulk-doc-converter
    runtime: docker
    plan: standard
    healthCheckPath: /health
    disk:
      name: docling-cache
      mountPath: /home/app/.cache/docling
      sizeGB: 1
    envVars:
      - key: OCR_BACKEND
        value: docling
      - key: DATA_DIR
        value: /data
      - key: JOB_TTL_SECONDS
        value: "3600"
      - key: MAX_UPLOAD_BYTES
        value: "209715200"
      - key: MAX_FILES_PER_JOB
        value: "50"
      - key: UPLOAD_RATE_LIMIT
        value: "10/minute"
```

Constraints captured here that a recreation must respect:

- **Plan**: `standard` is the minimum. Docling + the PyTorch CPU stack are ~2 GB resident; the Starter tier's 512 MB will OOM on the first PDF. Pro is only needed if multiple concurrent jobs are expected.
- **Persistent disk**: the `docling-cache` mount keeps the layout, OCR, and table-recognition model weights warm across redeploys. Without it every redeploy pays ~60–90 s of model downloads on the first request.
- **Ephemeral `/data`**: per-job inputs and outputs live under `/data/jobs/<id>/`. A redeploy or restart wipes in-flight jobs by design; this is acceptable in the current scope (no PHI, no compliance promise around durability).
- **Health check path**: `/health` returns 200 with `{"status":"ok","ocr_backend":"docling","version":"<x>"}`. Render routes load balancer probes here.
- **HTTPS**: provisioned automatically on `*.onrender.com`. Custom domains are configured in the Render dashboard, not in `render.yaml`.

#### New environment variables

In addition to the existing `OCR_BACKEND` and `MISTRAL_API_KEY` documented in 5.4, the upload pipeline adds:

| Name | Type | Default | Semantics |
|---|---|---|---|
| `DATA_DIR` | string | `/tmp/bulk_doc_converter` | Root directory for per-job `in/` and `out/` subdirectories. In Render, set to `/data` and mount the disk there if persistence is desired (it is not, in the current spec). |
| `JOB_TTL_SECONDS` | integer | `3600` | After a job reaches a terminal state (`completed` or `failed`), the sweeper removes its on-disk artifacts and in-memory record this many seconds later. |
| `MAX_UPLOAD_BYTES` | integer | `209715200` (200 MiB) | Hard cap on the aggregate byte count of a single `POST /upload` request. Enforced as bytes are read from the multipart stream; over-limit requests are rejected with `413` after rollback of any persisted partial state. |
| `MAX_FILES_PER_JOB` | integer | `50` | Maximum file count per upload. Enforced before any bytes are read; over-limit returns `413`. |
| `UPLOAD_RATE_LIMIT` | string | `10/minute` | `slowapi`-compatible quota string, keyed by client IP. Only enforced on `POST /upload`. |

`.env.example` carries the new keys with their defaults so a fresh checkout can copy it to `.env` without further reference.

#### Public-surface hardening

Because the deployed URL is reachable by anyone on the internet (single-tenant ≠ access-controlled), the spec requires the following minimum hardening, all implemented in `app/main.py`:

- **Rate limiting** on `POST /upload` via `slowapi` (`Limiter(get_remote_address)`); responses to over-limit clients are `429` with body `{"detail":"rate limit exceeded"}`.
- **Per-request file count and byte cap** as listed above; rejected with `413` before the request body is fully consumed where possible.
- **Filename sanitization** (`jobs.py:_safe_filename`) and **path-traversal guard** (every write asserts `dest.is_relative_to(job.input_dir)`).
- **Structured logging** via the stdlib `logging` module at `INFO` level. Render's dashboard captures stdout; `print()` statements introduced in earlier scaffolding were replaced with explicit `log.info(...)` calls so the production logs are filterable.

#### Local Docker verification

A recreation must support this end-to-end recipe before promoting to Render:

```bash
docker build -t bulk-doc-converter .
docker run --rm -p 8000:8000 \
  -v $(pwd)/.docling-cache:/home/app/.cache/docling \
  -e OCR_BACKEND=docling \
  bulk-doc-converter

# in another shell:
curl http://127.0.0.1:8000/health
# expect: {"status":"ok","ocr_backend":"docling","version":"..."}

curl -F 'files=@test_data/sbc_excerpt.pdf' \
     -F 'files=@test_data/policy.md' \
     http://127.0.0.1:8000/upload
# expect: 202 + {"job_id":"...","status":"queued","files_received":2}

# poll the returned job_id:
curl http://127.0.0.1:8000/jobs/<job_id>
# poll until status == "completed", then:
curl -OJ http://127.0.0.1:8000/jobs/<job_id>/download
unzip -l <job_id>.zip   # should list 2 .md files
```

#### Render deployment workflow

1. Commit `Dockerfile`, `.dockerignore`, `render.yaml`, and the updated `app/` to the default branch (or any feature branch — Render supports per-branch services).
2. In the Render dashboard, create a new web service from blueprint, point it at the repo.
3. First deploy will be slow (~10–15 min): wheel cache cold, Docling model weights pulled on first request.
4. After the deploy reports healthy, open the service URL in a browser and exercise the UI end-to-end with the same fixtures used for local verification.

## 6. Testing & validation

The project has no automated test suite. Validation is performed by (a) running the fixture generator to produce known inputs, (b) starting the server, and (c) issuing the smoke-test request described below. This section is normative for that procedure.

### 6.1 Test fixtures generator

`scripts/gen_test_data.py` is a **developer utility, not part of the runtime**. It writes six representative documents into `./test_data/` (relative to the repository root) to exercise each branch of the input-kind classifier and each text-extraction path.

Run it from the project's virtual environment after installing the extra `reportlab` dependency:

```bash
.venv/bin/pip install reportlab   # one-time, dev-only
.venv/bin/python scripts/gen_test_data.py
```

The script is idempotent: each invocation overwrites the existing files. It prints one line per generated file with name and byte size.

The six fixtures and what each one exercises:

| File                            | Purpose                                                                                                                                                                                                                                                                                                              |
|---------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `test_data/intro.txt`           | Plain-text passthrough. Three short lines of UTF-8 text. The extractor must read the file and emit the bytes unchanged (modulo line-ending normalization, which the project does not do).                                                                                                                            |
| `test_data/policy.md`           | Markdown passthrough. Must **round-trip byte-identically** — the extractor returns the file body untouched and the writer writes it to a `.md` of the same name in the output tree.                                                                                                                                  |
| `test_data/benefits.csv`        | CSV with one header row (`service,in_network_copay,out_of_network_copay`) and four data rows. Exercises the CSV-to-pipe-delimited-table extractor.                                                                                                                                                                   |
| `test_data/plan_summary.docx`   | DOCX containing an H1 heading ("2026 Plan Summary"), two H2 headings ("Preventive Care", "Prescription Drugs"), three body paragraphs, and one 5-row × 3-column drug-tier table. Exercises the `python-docx` flatten-and-tabulate path: ordered paragraphs followed by tables emitted as pipe-delimited markdown.   |
| `test_data/rates.xlsx`          | XLSX with two sheets: `Premiums` (4 rows × 3 cols) and `Networks` (3 rows × 3 cols). Exercises the multi-sheet header rendering branch of the `openpyxl` extractor — each sheet becomes its own section.                                                                                                             |
| `test_data/sbc_excerpt.pdf`     | Synthetic PDF built with `reportlab`. Contains a title, an intro paragraph, a 6-row × 3-column benefits table, and a closing paragraph. Exercises the OCR roundtrip — the only fixture whose output depends on the configured `OCR_BACKEND`.                                                                         |

The exact bytes of each fixture are deterministic given a fixed `reportlab`/`python-docx`/`openpyxl` version, but the **PDF in particular** is not byte-identical across reportlab versions because reportlab embeds timestamps and randomized object IDs. Treat the fixtures as semantically fixed (same content), not bit-fixed.

### 6.2 Smoke test recipe

This is the canonical end-to-end validation. It assumes the venv is built (section 5.4), `.env` is present or the defaults suffice (section 5.6), and the fixtures have been generated (section 6.1).

Step 1 — start the server in the background (any port is fine; `8000` used here):

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 &
```

Step 2 — wait for `/health` to return `200 OK`. The expected body is:

```json
{"status":"ok","ocr_backend":"docling"}
```

If `OCR_BACKEND=mistral` was set, the `"ocr_backend"` field will read `"mistral"` instead. Anything else is a failure.

```bash
until curl -sf http://127.0.0.1:8000/health >/dev/null; do sleep 1; done
curl -s http://127.0.0.1:8000/health
```

Step 3 — issue the conversion request:

```bash
curl -X POST http://127.0.0.1:8000/convert \
  -F input_dir=./test_data \
  -F output_dir=./out
```

Step 4 — verify the response. Expected outcome:

- HTTP status code `200`.
- JSON response with a `results` array whose length is exactly **6** (one entry per fixture).
- Every entry's `status` field equals `"processed"` (no `"failed"`, no `"skipped"`).
- The top-level `graphify_hint` field is a non-empty string of the form `Run \`/graphify <abs-output-dir> --obsidian --wiki\` to build the knowledge graph + Obsidian vault.`
- Six output files exist under `./out/`: `intro.md`, `policy.md`, `benefits.md`, `plan_summary.md`, `rates.md`, `sbc_excerpt.md`.

A passing smoke test is the only acceptance gate this project defines today.

#### 6.2.1 Expected response shape

The structure of the JSON body returned by `/convert` is defined by `app/models.py:ProcessFolderResponse`. The smoke test should assert all of the following:

- `input_dir` (string): absolute, resolved path to the input directory the server actually read.
- `output_dir` (string): absolute, resolved path to the output directory the server actually wrote to.
- `results` (array of objects): one entry per file discovered by `walk()`. For the bundled fixtures this array has length 6.
  - Each entry has `path` (relative to `input_dir`), `kind` (one of `text | csv | docx | xlsx | ocr | skip`), `status` (one of `processed | skipped | failed`), and either `output_path` (set when `status == "processed"`) or `reason` (set when `status == "skipped"` or `"failed"`).
  - For the bundled fixtures, the expected `(path, kind, status)` triples are: `(intro.txt, text, processed)`, `(policy.md, text, processed)`, `(benefits.csv, csv, processed)`, `(plan_summary.docx, docx, processed)`, `(rates.xlsx, xlsx, processed)`, `(sbc_excerpt.pdf, ocr, processed)`. Array order matches `sorted(root.rglob("*"))` (lexicographic by relative path).
- `graphify_hint` (string): non-empty.

A recreation that returns the same set of output files but in a different response order, or with a different field name, fails the smoke test by definition. The schema is the contract.

#### 6.2.2 Failure modes the smoke test should expose

- **Wrong OCR backend reported in `/health`:** indicates `.env` was not loaded or `OCR_BACKEND` is being read from the wrong source.
- **Result count not 6:** indicates the filesystem walker is filtering files differently, or a fixture failed to generate.
- **Any `status == "failed"`:** the `reason` field contains the exception message. Common first-run cause is a network failure during Docling model download; re-running after the download completes usually resolves it.
- **Any `status == "skipped"`:** indicates one of the fixtures has an extension the walker does not classify. This would be a regression: all six fixture extensions (`.txt`, `.md`, `.csv`, `.docx`, `.xlsx`, `.pdf`) are explicitly handled.
- **Hidden-dot-prefix in `test_data`:** the walker skips any path component starting with `.` (see `local_fs.py:walk`). If you accidentally write fixtures into `./test_data/.something/` they will be invisible to the converter.

### 6.3 Reference output parity

The repository ships a set of reference outputs in `./out/` produced by the verified flow on the verified platform. These are the canonical artifacts to diff against during recreation. Section organization for the assembled spec places the full text-native reference outputs in **Appendix C** of the final document (not in this section).

Parity rules differ by file type:

- **Text-native files** (`.txt`, `.md`, `.csv`, `.docx`, `.xlsx`): the recreated output must be **byte-identical** to the reference. The corresponding extractors are deterministic — `extract_text` is a passthrough, `extract_csv`, `extract_docx`, and `extract_xlsx` walk their inputs in a fixed order with a fixed serialization format. A mismatch on any of these indicates a real bug in the recreation.
  - `intro.txt` → `intro.md`: byte-identical passthrough.
  - `policy.md` → `policy.md`: byte-identical passthrough (the input is already markdown).
  - `benefits.csv` → `benefits.md`: pipe-delimited markdown table, deterministic.
  - `plan_summary.docx` → `plan_summary.md`: paragraphs in document order, tables flattened to pipe-delimited markdown.
  - `rates.xlsx` → `rates.md`: per-sheet H2 headings followed by pipe-delimited cells.

- **OCR output** (`sbc_excerpt.pdf` → `sbc_excerpt.md`): **structural equivalence, not byte equivalence**. Docling's output depends on model weights, library versions, and minor non-determinism in layout analysis. A passing recreation must produce output that is structurally equivalent to the reference:
  - Same heading text (case- and whitespace-insensitive).
  - Same number of cells in the benefits table, same row order, same column order.
  - All dollar amounts and percentages from the source preserved verbatim.
  - Paragraph order preserved.
  Use this checklist rather than `diff -q` for the PDF output.

### 6.4 Cold-start expectations

Timing is informational, not a hard requirement, but is documented here so a recreator knows what is normal.

- **Docling first PDF in a process:** 30–90 seconds on a typical laptop CPU. This window includes (a) lazy import of `torch` and downstream PyTorch modules, (b) construction of `DocumentConverter` (which initializes layout and OCR pipelines), and (c) on a cold machine, downloading model weights into `~/.cache/docling`. Subsequent invocations on the same machine skip the download but still pay the import cost (which only happens once per process, so it's only relevant on the very first PDF).
- **Docling subsequent PDFs in the same process:** approximately 3–10 seconds per PDF for fixtures of comparable size to `sbc_excerpt.pdf`. Scales roughly with page count and layout complexity.
- **Mistral OCR:** no cold start, but pays network round-trip latency per page (typically 1–3 seconds per page, dominated by the network). No model download; no local CPU work beyond HTTP and JSON parsing.
- **DOCX, XLSX, CSV, TXT, MD:** sub-second across the board. No model loading involved.

The first run of the smoke test on a fresh machine should therefore be expected to take roughly 1–2 minutes; subsequent runs of the same smoke test against the same process should complete in well under 30 seconds.

A second cold-start consideration worth flagging: `_make_ocr_backend()` runs at **module import time**, not at request-handling time. That means Uvicorn's startup banner ("Application startup complete.") will not print until Docling's `DocumentConverter()` constructor has returned. On a cold machine where the model download has not yet happened, `/health` therefore does not become reachable for the full 30–90 seconds. The smoke test's `until curl -sf /health` loop is correct for this reason — do not replace it with a fixed `sleep`.

### 6.5 Cleanup and `.gitignore` posture

The project's `.gitignore` is intentionally minimal. Its current contents are exactly:

```
.env
service-account.json
```

That is the whole file. Two consequences follow:

1. **`./out/` and `./test_data/` are not ignored.** They are tracked by default. The repository in fact ships a populated `./out/` and `./test_data/` so that a recreator has reference outputs to diff against without re-running the pipeline. If a future maintainer wants these to be generated locally rather than committed, they must (a) add the directories to `.gitignore` and (b) document the regeneration command (`scripts/gen_test_data.py` for inputs, the smoke test for outputs).
2. **Build/runtime byproducts are not ignored either.** `.venv/`, `cache/` (if any backend writes there), `graphify-out/`, and `__pycache__/` directories should logically be gitignored but currently are not. A recreation may add them to `.gitignore` without changing project behavior. The spec calls out this as a real gap but **does not enlarge the `.gitignore` retroactively** because the existing repository contents are what shipped and the spec must describe reality.

Cleanup is therefore manual:

```bash
rm -rf ./out ./test_data            # if you want to regenerate from scratch
rm -rf .venv                        # to rebuild the environment
# graphify-out, cache, __pycache__: remove if present and not needed
```

### 6.6 Manual extractor verification

In addition to the end-to-end smoke test, a recreator can verify individual extractors in isolation. This is useful when the smoke test fails and you want to localize the regression. All commands assume the venv is active and the fixtures have been generated.

Text passthrough:

```bash
.venv/bin/python -c "from app.services.textextract import extract_text; \
print(extract_text(__import__('pathlib').Path('test_data/intro.txt')))"
```

CSV to pipe-delimited:

```bash
.venv/bin/python -c "from app.services.textextract import extract_csv; \
print(extract_csv(__import__('pathlib').Path('test_data/benefits.csv')))"
```

Expected first line of CSV output: `service | in_network_copay | out_of_network_copay`.

DOCX paragraphs and tables:

```bash
.venv/bin/python -c "from app.services.textextract import extract_docx; \
print(extract_docx(__import__('pathlib').Path('test_data/plan_summary.docx')))"
```

Expected: heading text as plain paragraphs, then drug-tier table rows joined with ` | `.

XLSX multi-sheet:

```bash
.venv/bin/python -c "from app.services.textextract import extract_xlsx; \
print(extract_xlsx(__import__('pathlib').Path('test_data/rates.xlsx')))"
```

Expected: a `## Sheet: Premiums` block followed by a `## Sheet: Networks` block.

OCR (the slow one, exercises Docling):

```bash
.venv/bin/python -c "from app.services.docling_ocr import DoclingService; \
print(DoclingService().convert(__import__('pathlib').Path('test_data/sbc_excerpt.pdf')))"
```

Expected: markdown containing the title, the intro paragraph, a benefits table (Markdown or HTML form depending on Docling version), and the closing paragraph. This single command exercises the entire OCR cold-start path documented in section 6.4 and is the fastest way to confirm Docling is functional before involving the HTTP layer.

### 6.7 No automated test suite

There is **no `pytest` configuration, no `tests/` directory, and no CI workflow checked into this repository**. Validation is the smoke test in section 6.2 and nothing else.

This is acknowledged as a gap. A future-state test plan would, at minimum:

- Replace the OCR backend with a stub `class FakeOCR: def convert(self, path): return "STUB"` in test fixtures and assert that `/convert` returns the expected `FileResult` shape for every fixture kind.
- Assert byte-identity for the deterministic extractors (text, csv, docx, xlsx).
- Assert HTTP error contracts (400 on non-directory `input_dir`, structured `failed` entries on extractor exceptions).
- Run under `pytest` with `httpx.AsyncClient` against the FastAPI app via `app.dependency_overrides` to inject the fake OCR backend.

These tests **do not exist today**. They are listed here as a roadmap item only; a recreation of this spec is not required to implement them. A recreation **is** required to make the smoke test in section 6.2 pass.

### 6.8 Validation summary

To summarize sections 6.1 through 6.7, a recreation of this project is considered correct when:

1. The fixture generator runs to completion and produces six files in `./test_data/` (section 6.1).
2. The server starts and `/health` returns `{"status":"ok","ocr_backend":"<configured backend>"}` (section 6.2).
3. `POST /convert` against `./test_data` returns HTTP 200, six `FileResult` entries, all with `status="processed"`, and a non-empty `graphify_hint` (section 6.2).
4. The five text-native output files are byte-identical to the references in `./out/` (section 6.3).
5. The OCR output file is structurally equivalent to the reference: same headings, same table cells in the same order, same numeric content preserved (section 6.3).

No other automated criterion is part of the acceptance contract today.

A recreation that satisfies criteria 1 through 5 is, by the definition this spec provides, complete. A recreation that fails any of them must be debugged before claiming spec compliance; the most common point of failure on a fresh machine is criterion 2 (server fails to start because Docling model download is in progress or has failed), followed by criterion 5 (OCR output ordering differs because of a Docling version skew). Criteria 1, 3, and 4 are deterministic and a failure there indicates a real bug in the recreation's code, not an environmental issue.

Recreators who hit criterion-5 mismatches should compare the failing output against the reference using the structural-equivalence checklist in section 6.3 rather than `diff`. If every cell from the source table is present in the output (regardless of formatting), the OCR path is working correctly even if the markdown serialization differs.

Recreators who hit criterion-4 mismatches should focus on extractor determinism: `extract_csv` joins with the literal three-character sequence `" | "`, `extract_xlsx` prefixes each sheet with `## Sheet: <title>`, and `extract_docx` emits paragraphs and table rows in document order with double-newline separators. Any deviation in those serialization details will break byte-identity against the references shipped in `./out/`.

The references in `./out/` were produced by the verified flow on the verified platform with `OCR_BACKEND=docling`. They are the ground truth for criteria 4 and 5 and should not be regenerated casually; a recreation should diff against the committed references, not against locally regenerated ones.

If a regeneration of the references is genuinely required (for example because an upstream Docling version skew makes the OCR reference unattainable on the current platform), the regeneration command is the smoke test in section 6.2 itself: a successful run writes fresh outputs to `./out/`. Commit those, note the Docling version in the commit message, and treat them as the new ground truth going forward.

---

## 7. Output format specification

This section defines, exhaustively, the shape and content of every file the service writes under `<output_dir>` during a `/convert` run. A recreator MUST be able to reproduce byte-for-byte (or, in the case of OCR, structurally-equivalent) outputs from these rules.

### 7.1 Output tree mirroring rule

The output directory MUST mirror the input directory's relative structure.

For every input file at `<input_dir>/<rel_path>` that is not skipped by the dispatch rules in Section 5, the service writes exactly one corresponding output file at:

```
<output_dir>/<rel_path with final extension replaced by .md>
```

Concretely:

- `<input_dir>/intro.txt`           -> `<output_dir>/intro.md`
- `<input_dir>/billing/rates.xlsx`  -> `<output_dir>/billing/rates.md`
- `<input_dir>/forms/sbc.pdf`       -> `<output_dir>/forms/sbc.md`

Parent directories under `<output_dir>` are created as needed (recursive `mkdir -p` semantics). The service MUST NOT write files outside `<output_dir>`. The service MUST NOT delete pre-existing files in `<output_dir>`; it overwrites in place.

If a file is skipped (extension not in the dispatch table, or it is a hidden / system file per Section 5), nothing is written for it. There is no marker file, no empty `.md`, no log entry inside the output tree.

### 7.2 File-extension transform

The input's final extension is unconditionally replaced by `.md`, regardless of how the body is produced (OCR-derived, library-extracted text, or byte-for-byte passthrough). This is a deliberate uniformity choice: downstream tooling (graphify, RAG indexers, code review) sees one extension and one MIME profile.

- `report.csv`        -> `report.md`
- `plan_summary.docx` -> `plan_summary.md`
- `intro.txt`         -> `intro.md`
- `notes.md`          -> `notes.md`  (no-op rename, body is passthrough)
- `sbc.pdf`           -> `sbc.md`

Collision handling: if two input files would map to the same output `.md` path (for example `report.csv` and `report.xlsx` in the same directory), the implementation does NOT detect or guard this. The directory walk is depth-first / lexicographic and the later-walked file wins, silently overwriting the earlier one. This is a known limitation and a candidate for a future "collision detection" pass.

### 7.3 Encoding

All output files are written as UTF-8 with no BOM. Newlines are LF (`\n`). The service does not emit CRLF. Files are not explicitly newline-terminated; the trailing newline structure is whatever the format's builder produced (typically a `"\n\n".join(parts)` or `"\n".join(rows)`).

### 7.4 Why markdown

Markdown is the universal output format for four converging reasons:

1. **Graphify ingests `.md` natively.** The handoff in Section 8 expects a tree of markdown files; no transcoding step is required between `/convert` and `/graphify`.
2. **Human-reviewable.** An operator can `cat` or open any output file and immediately judge fidelity. Validation does not require special tools.
3. **Structure preservation.** Headings, tables, lists, and paragraph flow survive the conversion. Downstream NLP (entity extraction, chunking, embedding) benefits from this structure without parsing per-format payloads.
4. **Plain text.** Outputs are diff-friendly, grep-friendly, copyable, and version-controllable. No binary dependencies are introduced into the downstream pipeline.

Markdown is not chosen for visual rendering quality; it is chosen as a structured plain-text lingua franca.

### 7.5 Per-(kind, backend) output format reference

Each input file is dispatched to one of six `(kind, backend)` combinations. Section 5 specifies the dispatch table; this subsection specifies the exact markdown shape each combination produces.

#### 7.5.1 `text` kind (`.txt`, `.md`, `.markdown`)

**Shape:** byte-identical passthrough of the source file content.

- The file is read with `Path.read_text(encoding="utf-8", errors="replace")`. Invalid byte sequences are replaced with the Unicode replacement character (U+FFFD). No other transformation is applied.
- No header, footer, frontmatter, or wrapping. No normalization of newlines (the OS-native newlines are preserved as read by Python; on Linux this means LF in, LF out).
- No trimming, no rewriting of links, no fixup of headings.

The only operation that distinguishes `text` kind from a literal `cp` is the extension rename (`.txt` -> `.md`). For `.md` and `.markdown` inputs, the output file is byte-for-byte the input.

Illustrative snippet (`intro.txt` -> `intro.md`):

```
Welcome to Acme Health Plan.

Members may visit any in-network provider without a referral.
Emergency room visits are covered worldwide.
```

#### 7.5.2 `csv` kind (`.csv`)

**Shape:** each row of the CSV becomes one line in the output. Cells are joined with the literal three-character separator ` | ` (ASCII space, pipe, ASCII space). Rows are joined with LF.

Rules:

- The file is opened with `encoding="utf-8", errors="replace", newline=""` and parsed by `csv.reader` with default dialect (comma-delimited, double-quote quoting).
- **The header row receives no special treatment.** It is just the first line in the output. The service does NOT emit a markdown table separator (`---`) after it. Consumers that need a real markdown table must add structure themselves; the contract here is a flat dump.
- Empty rows produce an empty line (the join of an empty cells list yields the empty string).
- No trailing newline is appended beyond what the last row contributes.

Illustrative snippet (`benefits.csv` -> `benefits.md`):

```
service | in_network_copay | out_of_network_copay
primary_care | 25 | 80
specialist | 50 | 150
urgent_care | 40 | 120
emergency_room | 300 | 300
```

#### 7.5.3 `xlsx` kind (`.xlsx`)

**Shape:** for each worksheet in workbook order, emit a level-2 markdown heading `## Sheet: <worksheet title>` followed by a blank line and rows in the same ` | ` style as CSV. Sheets are separated by a blank line.

Rules:

- The workbook is loaded with `openpyxl.load_workbook(path, data_only=True, read_only=True)`. `data_only=True` resolves cached formula values; formulas themselves are not preserved.
- Worksheets are iterated in `wb.worksheets` order (the workbook's natural sheet order).
- Within a sheet, rows are iterated via `iter_rows(values_only=True)`.
- Each cell is stringified: `None` becomes the empty string, all other values become `str(v)` — i.e. Python's default `str()`. There is no number formatting, no currency, no thousands separators, no date formatting. A cell holding `1180` renders as `1180`, not `$1,180.00`.
- **Rows where every cell is blank after `.strip()` are skipped entirely.** A row with at least one non-whitespace cell is kept.
- The sheet heading `## Sheet: <title>` is emitted ONLY if the sheet contributes at least one non-blank row. Sheets that are entirely empty produce no heading and no separator.
- An empty workbook (no sheets contribute rows) produces an empty file (zero bytes after the final `"\n\n".join([])`).
- Sheets are joined with `"\n\n"`; within a sheet, the heading and the rows-block are joined with `"\n\n"` (one blank line between heading and first row), and rows within the block are joined with `"\n"` (no blank lines between rows).

Illustrative snippet (`rates.xlsx` -> `rates.md`):

```
## Sheet: Premiums

tier | monthly_premium | employer_contribution
Employee only | 425 | 300
Employee + spouse | 812 | 550
Employee + family | 1180 | 800

## Sheet: Networks

network | states | hospitals_count
Acme National | all | 4200
Acme Regional | CA,OR,WA | 380
```

#### 7.5.4 `docx` kind (`.docx`)

**Shape:** paragraphs are emitted in document order, then tables are emitted in document order; each non-empty unit becomes one line, separated from neighbors by a blank line.

Rules:

- The document is loaded with `python-docx`'s `Document(path)`.
- Iteration order is: first all `doc.paragraphs` (in order), then all `doc.tables` (in order). The paragraph stream is exhausted before any table is emitted. **Tables are NOT interleaved at their visual position in the document.** They are appended after the paragraph stream. This is a known fidelity gap.
- Each paragraph is stripped (`.strip()`) and skipped if empty. Non-empty paragraphs are appended as-is.
- **Heading levels are flattened to plain text.** A `Heading 1` paragraph and a body paragraph are indistinguishable in the output — both appear as a bare line. This is a known limitation and a candidate for improvement (a future pass could inspect `paragraph.style.name` and prepend the appropriate `#`/`##`/`###`).
- Tables are emitted one row per line, cells stripped and joined with ` | ` (same convention as CSV/XLSX). No header separator row is emitted.
- All parts (paragraphs + table rows) are joined with `"\n\n"`. The output therefore consists of single lines separated by blank lines throughout — there is no contiguous-paragraph block.

Illustrative snippet (`plan_summary.docx` -> `plan_summary.md`):

```
2026 Plan Summary

This document summarizes covered benefits for the Acme PPO Gold plan.

Preventive Care

Annual wellness exams, immunizations, and routine screenings are covered at 100% with no member cost share when received from in-network providers.

Prescription Drugs

Tier | Retail (30 day) | Mail order (90 day)

Generic | $10 | $20

Preferred brand | $40 | $80

Non-preferred brand | $70 | $140

Specialty | $150 | N/A
```

Note that the table rows are blank-line-separated, not contiguous — this is a direct consequence of `"\n\n".join(parts)`. Downstream consumers that need a "tight" markdown table must re-stitch.

#### 7.5.5 `ocr` kind, backend = `docling`

**Shape:** verbatim output of `DoclingDocument.export_to_markdown()` for the converted PDF / image.

Characteristics of Docling's markdown:

- Atx-style headings (`##`, `###`, ...) reflecting the detected document hierarchy.
- GitHub-flavored markdown tables: pipe-delimited rows with a separator row of dashes after the header (e.g. `|---|---|---|`). Column widths are padded with spaces for visual alignment, but the pipe characters are the structural delimiter.
- Paragraph flow is preserved; runs of body text become regular paragraphs separated by blank lines.
- Lists are emitted as standard markdown lists when detected.
- **No page boundary markers are inserted.** The output is a single continuous markdown stream; page breaks in the source PDF are not visible in the output.

Illustrative snippet (`sbc_excerpt.pdf` -> `sbc_excerpt.md`, docling backend):

```
## Acme PPO Gold - Benefits at a Glance

The following table summarizes member cost-sharing for common covered services. All figures assume in-network providers unless noted.

| Service            | Deductible Applies   | Member Cost          |
|--------------------|----------------------|----------------------|
| Preventive care    | No                   | $0                   |
| Primary care visit | No                   | $25 copay            |
| Specialist visit   | No                   | $50 copay            |
| Inpatient hospital | Yes                  | 20% after deductible |
| Emergency room     | Yes                  | $300 copay then 20%  |

Out-of-network services are subject to a separate deductible and balance billing may apply. See the full SBC for details.
```

#### 7.5.6 `ocr` kind, backend = `mistral`

**Shape:** Mistral's per-page OCR output, each page wrapped in literal page-boundary markers, with HTML tables inlined and image markdown stripped.

Production pipeline (`app/services/mistral.py` and `app/services/postprocess.py`):

1. The file bytes are uploaded to Mistral with `purpose="ocr"`.
2. `client.ocr.process(model="mistral-ocr-latest", document={"type": "file", "file_id": uploaded.id}, table_format="html")` is called.
3. The response carries a `pages` list. Each page exposes `.index` (0-based), `.markdown` (the page body), and `.tables` (a list of objects with `.id` and `.content`).
4. For each page, the postprocessor:
   - **Inlines tables.** Page markdown may contain references of the form `[tbl-N.html](tbl-N.html)` (a markdown link whose label and target are the same). For each such reference, the matching table object's `.content` (raw HTML) is substituted in place. The regex used is `\[(tbl-\d+\.html)\]\(\1\)`. Tables that are not referenced are not emitted.
   - **Strips image markdown** at line granularity. Any line matching `^\s*!\[[^\]]*\]\([^)]+\)\s*\n?` is removed. This means image references that share a line with surrounding text are NOT stripped — only standalone image lines. This is by design.
   - Trims trailing whitespace via `.strip()`.
5. The processed body is wrapped:

```
[[START OF PAGE N]]

<body>

[[END OF PAGE N]]
```

   where N is the 1-based page index (`p.index + 1`).
6. All page blocks are joined with `"\n\n"` and returned as the final markdown string.

The page-marker convention is **Mistral-only** today. Downstream consumers that depend on page boundaries (RAG citation with page-level provenance, page-level highlighting, per-page redaction) will only work for Mistral-sourced files.

Illustrative snippet (single-page PDF, mistral backend):

```
[[START OF PAGE 1]]

## Acme PPO Gold - Benefits at a Glance

The following table summarizes member cost-sharing for common covered services.

<table>
  <tr><th>Service</th><th>Deductible Applies</th><th>Member Cost</th></tr>
  <tr><td>Preventive care</td><td>No</td><td>$0</td></tr>
  ...
</table>

[[END OF PAGE 1]]
```

(The inlined table is raw HTML because Mistral was invoked with `table_format="html"`. Markdown renderers will display this correctly; programmatic consumers must accept that OCR-derived tables are HTML, not pipe-delimited markdown.)

### 7.6 Page-marker asymmetry (known gap, roadmap)

The two OCR backends produce structurally different artifacts:

| Property                  | docling backend                       | mistral backend                                    |
|---------------------------|---------------------------------------|----------------------------------------------------|
| Page boundaries           | Not marked                            | `[[START OF PAGE N]]` / `[[END OF PAGE N]]`         |
| Tables                    | GFM pipe tables                       | Raw HTML inlined from `tbl-N.html` references       |
| Images                    | Behavior per Docling defaults         | Line-anchored image markdown stripped               |
| Headings                  | Atx-style, hierarchy preserved        | Whatever Mistral emits per page                     |
| Locality of failure       | Whole document fails or succeeds      | Per-page                                            |

This asymmetry is a real interoperability hazard. A consumer that searches output files for `[[START OF PAGE` to extract page-scoped context will silently find nothing in docling-produced files. A consumer that consumes GFM tables natively will need an HTML parser for mistral-produced tables.

**Roadmap item (do not implement in this spec):** teach the docling backend to emit equivalent `[[START OF PAGE N]]` / `[[END OF PAGE N]]` markers by iterating `DoclingDocument`'s page collection and exporting page-scoped slices. This would normalize the two backends behind a single contract. The implementation is out of scope for this document; it is mentioned here only to record the gap.

Similarly, normalizing mistral's HTML tables into GFM pipe tables (or vice versa) is a candidate cleanup pass for a future `postprocess` revision.

**Implications for downstream consumers (informative):**

- A RAG indexer that chunks by page MUST detect the backend before chunking. If `[[START OF PAGE` is present anywhere in the file, chunk by markers; otherwise, fall back to length-based or heading-based chunking.
- A citation system that surfaces "page N of <doc>" provenance to end users SHOULD label docling-sourced citations as "section" rather than "page" to avoid misleading users.
- An evaluation harness that diffs two conversions of the same PDF (one per backend) MUST normalize away the page markers before computing similarity; otherwise the diff will be dominated by structural-marker noise rather than substantive OCR differences.

These implications are out of scope for the conversion service itself but are flagged here because the spec's first downstream consumer (graphify) and the second (a future RAG layer) will both need to make these choices.

### 7.7 Failure modes

This subsection is informative; it is referenced from Section 6 (error handling).

- **Unreadable file (permission, missing).** The dispatcher raises an exception; no `.md` is written for that file. The `/convert` response records the failure under `errors`. Other files in the batch are unaffected.
- **Corrupt office document.** `python-docx` / `openpyxl` raise. Same handling as above.
- **OCR backend timeout or 5xx.** The service does not retry transparently; the file is recorded as failed. Re-running `/convert` will reprocess it.
- **Empty source file.** Produces an empty `.md` file (zero bytes). This is not an error.

### 7.8 Write semantics

The recreator should implement the per-file write as a direct `Path.write_text(body, encoding="utf-8")` against the final destination path. Atomic write (write-to-tempfile + rename) is NOT required by this contract; the service does not advertise crash-safety for concurrent partial writes. If the service is killed mid-batch, partial / truncated `.md` files may remain in `<output_dir>` for the file that was being written at the moment of termination. Re-running `/convert` with the same arguments will overwrite them.

The service does NOT lock the output directory. Two concurrent `/convert` runs against the same `<output_dir>` are unsupported. The behavior is undefined; do not rely on it.

### 7.9 Recreator verification checklist

A recreator can confirm Section 7 compliance against the six fixtures in `./test_data/` as follows:

1. Run `/convert` with `OCR_BACKEND=docling` against `./test_data/` writing to a scratch `./out_test/`.
2. Verify the output tree mirrors the input tree exactly. Each `.txt`, `.md`, `.csv`, `.xlsx`, `.docx`, and `.pdf` input has a corresponding `.md` output at the same relative path.
3. For the four non-OCR outputs (`intro.md`, `policy.md`, `benefits.md`, `plan_summary.md`, `rates.md`), `diff` against Appendix C must be empty.
4. For the OCR output (`sbc_excerpt.md`), structural inspection: the file contains at least one Atx heading, at least one GFM pipe table with a `|---|` separator row, no `[[START OF PAGE` markers (docling backend), and no image markdown.
5. The file size of every output is non-negative; no zero-byte files (other than for empty inputs).
6. Re-running `/convert` against the same input produces identical bytes for the five non-OCR outputs.

If `OCR_BACKEND=mistral` is used instead, step 4 changes: `sbc_excerpt.md` MUST contain exactly two markers (`[[START OF PAGE 1]]` and `[[END OF PAGE 1]]`) and SHOULD contain an inlined HTML `<table>` element. The number of `[[START OF PAGE N]]` markers MUST equal the number of `[[END OF PAGE N]]` markers and MUST equal the page count of the source PDF.

### 7.10 Upload-path delivery (zip artifact)

When files are processed via `POST /upload` instead of `POST /convert`, the same per-(kind, backend) output rules apply unchanged. The difference is in delivery:

- The `.md` files are written to `${DATA_DIR}/jobs/<job_id>/out/`, mirroring the relative paths of the uploaded files (after filename sanitization, which strips path separators — uploads cannot establish subdirectories in the current scope).
- Once the batch loop completes, `services.zip_out.zip_directory` zips that `out/` tree into `${DATA_DIR}/jobs/<job_id>/out.zip`. The zip preserves the relative paths and uses DEFLATE compression.
- `GET /jobs/<job_id>/download` streams that zip to the client with `Content-Type: application/zip` and `Content-Disposition: attachment; filename="<job_id>.zip"`.
- A recreation's zip MUST contain exactly one `.md` file per `processed` `FileResult` and zero entries for `skipped` or `failed` files.

The content of each `.md` file inside the zip is bit-identical to what `process_directory` would have written to a regular output directory; the zip is a packaging convenience, not a separate format.

---

## 8. Knowledge graph integration

This section specifies how the markdown corpus produced by `/convert` becomes a knowledge graph, and — equally importantly — what is intentionally NOT part of this service.

### 8.1 Two-stage operating model

The system is split into two stages with a hard boundary between them:

**Stage 1 — Conversion (this service).** The synchronous `/convert` endpoint walks `<input_dir>`, dispatches each file through Section 7's rules, and writes a uniform markdown corpus into `<output_dir>`. When `OCR_BACKEND=docling` (the default for local / PHI deployments), Stage 1 contains **no LLM call** and is fully deterministic given the same inputs, the same model weight versions, and the same library versions. When `OCR_BACKEND=mistral`, the only network call is to the Mistral OCR endpoint, which is a single-purpose vision model — not a general-purpose chat LLM. The endpoint's contract is: in -> tree of files; out -> tree of markdown files plus a JSON manifest.

**Stage 2 — Knowledge graph build (graphify, a separate tool).** Invoked **separately** by the operator from a Claude Code session (or any agent host that can dispatch subagents) by running `/graphify <output_dir> --obsidian --wiki`. Stage 2 performs LLM-driven semantic extraction: it reads every `.md` file, asks an LLM to identify entities, relationships, and communities, builds a graph data structure, exports it in multiple representations, and writes a human-readable audit report. Stage 2 is **not** triggered by Stage 1 and is not part of the FastAPI service.

This separation is intentional and load-bearing. The rationale (expanded in Section 9, summarized here):

- **Determinism.** Stage 1 must produce the same output for the same input. An LLM in the hot path would make every conversion non-reproducible.
- **Latency.** Stage 1 is bounded by document size and OCR throughput. An embedded LLM extraction stage would multiply latency by 10x–100x.
- **Provider coupling.** Embedding graphify inside the FastAPI service would force the deployment to bind to a specific LLM provider (Gemini, Anthropic, Ollama, etc.). Keeping the stages separate lets the operator choose the graph-stage provider per deployment without redeploying the conversion service.
- **Failure independence.** A graphify failure (rate limit, model outage, parse error) must never block Stage 1 from delivering the markdown corpus. The corpus is valuable on its own.
- **Runtime environment.** Graphify's LLM extraction is implemented as a fleet of subagents under Claude Code (or, alternatively, a direct Gemini CLI path). The agent runtime is fundamentally not a FastAPI worker. Putting them in the same process would mean importing an agent host into the web service.

### 8.2 What graphify produces

When the operator runs `/graphify <output_dir> --obsidian --wiki`, graphify creates a `graphify-out/` directory inside `<output_dir>` (or wherever the graphify CLI is configured to write — by default, alongside the corpus). The full produced tree:

```
<output_dir>/graphify-out/
  graph.json            <- canonical graph (nodes, edges, hyperedges, attributes)
  graph.html            <- single-file interactive force-directed viewer
  GRAPH_REPORT.md       <- human-readable audit
  manifest.json         <- file fingerprint manifest (used by --update)
  cost.json             <- cumulative LLM token-spend ledger
  cache/                <- per-file extraction cache (--update reuses unchanged files)
  obsidian/             <- Obsidian vault: one .md note per node + graph.canvas + index
  wiki/                 <- agent-crawlable wiki: one article per community + index.md
```

Per-artifact contract:

- **`graph.json`** — The canonical, programmatically-consumable representation. Contains node objects (with `id`, `label`, `type`, `attributes`, source-file provenance), edge objects (`source`, `target`, `relation`, `confidence`, `evidence`, `extraction_mode` of `EXTRACTED` or `INFERRED`), and hyperedges (group relationships spanning more than two nodes). This is the source of truth — every other artifact in `graphify-out/` is derived from it. Stable schema across graphify versions; safe to consume from code.
- **`graph.html`** — A self-contained HTML page (no external CDN dependencies at runtime; the JS is inlined). Open in a browser to explore the graph with force-directed layout, zoom, filter by community, click-through to node details. Intended for human exploration, not programmatic consumption.
- **`GRAPH_REPORT.md`** — A human-readable audit. Includes: corpus size check (warns if the corpus fits in a single context window and a graph may be overkill), summary counts, list of community hubs, "god nodes" (most-connected nodes — typically the core abstractions of the domain), surprising cross-community connections (especially inferred ones, flagged for verification), hyperedges, knowledge gaps (isolated nodes), and suggested questions the graph is uniquely positioned to answer. This file is the operator's primary "did the graph capture the domain correctly?" review surface.
- **`obsidian/`** — A working Obsidian vault. One `.md` note per node (filename equals node label, with characters sanitized for filesystem safety), one `_COMMUNITY_*.md` per community hub, and `graph.canvas` (JSON Canvas) giving a visual overview. Open the directory as an Obsidian vault to navigate by backlinks, search, and the built-in Obsidian graph view. Notes use wikilinks (`[[Other Node]]`) for cross-references.
- **`wiki/`** — An agent-crawlable wiki. One `.md` article per community plus an `index.md` entry point that lists communities and links to articles. Filenames use underscore-escaped community/node names (no spaces, no special characters) to make them robust under shell tools and agent crawlers. Use this when the consumer is an LLM agent doing retrieval, not a human in Obsidian.
- **`manifest.json`** — A file fingerprint manifest mapping input markdown paths to content hashes and extraction timestamps. Used by `/graphify <dir> --update` to detect which files changed since the last run and re-extract only those. The exact schema is graphify-internal and not constrained here; recreators of this service MUST NOT read or write `manifest.json`. The file is mentioned only so the recreator does not mistake it for a corpus file when designing the directory layout.
- **`cost.json`** — A cumulative token-spend ledger across runs. Each entry records `date` (ISO-8601 timestamp with timezone), `input_tokens`, `output_tokens`, and `files` (count of files processed in that run). The top-level object also carries `total_input_tokens` and `total_output_tokens` rollups. The ledger is append-only across `--update` runs so the operator can audit cumulative LLM spend over the lifetime of the project. Example shape (from the reference run against `./out/`):

  ```
  {
    "runs": [
      { "date": "2026-05-12T18:49:26.345953+00:00",
        "input_tokens": 23152,
        "output_tokens": 0,
        "files": 6 }
    ],
    "total_input_tokens": 23152,
    "total_output_tokens": 0
  }
  ```

  An `output_tokens` value of zero on a successful run reflects that the graphify pipeline batched its prompts in a mode that consumed input context but produced no chargeable output tokens for that particular run; this is a graphify implementation detail and is not constrained by this spec.
- **`cache/`** — Per-file extraction cache (entities, relationships, hyperedge proposals) keyed by content hash. `--update` consults this cache before issuing any LLM call.

### 8.3 How to invoke

The `/convert` response's `graphify_hint` field carries the canonical invocation string. The operator copies it verbatim into a Claude Code session.

**Canonical form** (from a Claude Code session, recommended):

```
/graphify /absolute/path/to/output_dir --obsidian --wiki
```

This runs the full pipeline under Claude Code subagents. No environment variables are required beyond what Claude Code itself needs.

**Direct CLI form** (from a shell, requires `GEMINI_API_KEY`):

```
GEMINI_API_KEY=... graphify /absolute/path/to/output_dir --obsidian --wiki
```

This runs the full pipeline against hosted Gemini without going through Claude Code.

Flags relevant to this spec:

- `--obsidian` — produce the `obsidian/` vault.
- `--wiki` — produce the `wiki/` directory.
- `--update` — incremental rebuild (see Section 8.4).

Other graphify flags (model selection, concurrency, provider) are documented in graphify's own skill file and are intentionally not enumerated here. This spec only constrains the **handoff contract**, not graphify's internals.

### 8.4 When to re-run

- **Full rebuild.** Required after a structural change to the corpus: large additions, large deletions, or a re-conversion that may have changed many `.md` bodies. Delete `graphify-out/` (or run graphify without `--update`) and let the pipeline start from scratch.
- **Incremental update.** `graphify <dir> --update` when a small number of files have changed. The manifest is consulted, only files whose content hash changed are re-extracted, and the graph, reports, vault, and wiki are re-rendered. The token ledger appends a new entry rather than overwriting.

There is no automatic trigger from Stage 1 to Stage 2; the operator must explicitly re-run graphify after re-running `/convert`.

### 8.5 What graphify is NOT (scope boundary)

Graphify is a **separate tool, outside this repository**. Its installation, model selection, prompt templates, extraction pipeline, community-detection algorithm, and HTML viewer template are documented in graphify's own skill file. They are not specified here and are not constrained by this document.

This spec only constrains the **handoff contract**:

- This service produces a directory of markdown files at `<output_dir>`, conforming to Section 7.
- Graphify consumes that directory.
- Graphify writes its artifacts into `<output_dir>/graphify-out/` (or a sibling path configurable by the operator).

A recreator of this service does NOT need to recreate graphify. A recreator of graphify does not need to recreate this service. The handoff is the corpus on disk.

### 8.6 Privacy considerations for the graph stage

Stage 1 (`/convert`) under `OCR_BACKEND=docling` keeps all data on the local machine — no network egress occurs. Under `OCR_BACKEND=mistral`, the source documents are uploaded to Mistral's hosted OCR endpoint.

Stage 2 (graphify) introduces an additional privacy decision that the operator MUST make explicitly:

- **Hosted Gemini (`GEMINI_API_KEY` set).** Document chunks leave the local machine and are sent to Google's Gemini API for entity / relationship extraction. This is acceptable for non-sensitive corpora and convenient for development. It is NOT acceptable for PHI without a BAA covering Gemini usage.
- **Claude Code subagents.** Document chunks are sent to Anthropic's API. Same caveat — fine for non-PHI, requires a BAA for PHI.
- **Fully local model (e.g. Ollama-backed provider).** All extraction runs on the operator's machine; no document text leaves the boundary. Lower accuracy than hosted frontier models, but the only option for PHI without a covering BAA.

Operators in a PHI context MUST choose either an LLM provider covered by a BAA or a fully local model. This decision is per-deployment and is configured in graphify, not in this service.

See Section 9 (architectural rationale) and Section 10 (privacy posture statement) for the full operating-environment matrix and the reasoning behind keeping Stage 1 LLM-free.

### 8.7 Handoff contract summary

The minimal, machine-checkable contract between Stage 1 and Stage 2 is:

- **Input to graphify.** A directory containing one or more `.md` files (at any depth). Non-`.md` files in the directory are ignored by graphify and may be present without harm.
- **No coupling on filenames.** Graphify treats every `.md` filename as an opaque label. It uses the file's path for provenance display in `GRAPH_REPORT.md` and for source citations, but no part of the pipeline parses semantic information out of the filename.
- **No coupling on intra-file structure.** Graphify does not require any specific heading, frontmatter, table format, or marker convention. The `[[START OF PAGE N]]` markers from the mistral backend are passed through and may appear in node provenance strings, but they are not required by graphify.
- **Output isolation.** All graphify artifacts live under `graphify-out/`. The recreator MUST NOT scatter graphify artifacts into `<output_dir>` at the top level — that would pollute the corpus and break `--update` detection, because graphify hashes the `.md` files it finds.

Practically: if a recreator follows Section 7 verbatim, the resulting `<output_dir>` is graphify-compatible without any additional adapter, marker insertion, or schema massage.

### 8.8 Anti-goals

To be explicit about what this service must NOT do regarding graphify:

- The `/convert` endpoint MUST NOT call out to graphify at the end of a run.
- The service MUST NOT bundle graphify, import graphify modules, or ship a graphify CLI.
- The service MUST NOT depend on `GEMINI_API_KEY` or any LLM credential beyond what's needed for the configured OCR backend.
- The service MUST NOT write into `<output_dir>/graphify-out/`; that namespace belongs to graphify.
- The service MUST NOT validate that the operator has graphify installed. The `graphify_hint` field in the `/convert` response is purely advisory text.

These anti-goals are what make Section 9's architectural posture (deterministic, LLM-optional data prep) achievable.

### 8.9 Operator workflow (end-to-end)

The intended day-1 workflow for an operator who has just stood up this service:

1. POST a tarball / directory reference of source documents to `/convert`.
2. Receive a JSON response with `output_dir`, per-file status, and `graphify_hint`.
3. Optionally spot-check a handful of files in `<output_dir>` to confirm fidelity (open them in a markdown viewer, diff against expectations).
4. Copy the `graphify_hint` command into a Claude Code session: `/graphify <output_dir> --obsidian --wiki`.
5. Wait for graphify to complete. Token spend appears in `<output_dir>/graphify-out/cost.json`.
6. Open `<output_dir>/graphify-out/GRAPH_REPORT.md` for a review of what was extracted. Open `graph.html` for visual exploration. Open the `obsidian/` directory as an Obsidian vault for backlinks-driven navigation.
7. On corpus changes, re-run `/convert` (which overwrites in place), then `graphify <output_dir> --update` to incrementally refresh.

Steps 4–7 happen outside the bounds of this service; they are listed only to make the seam between the stages concrete.

### 8.10 Graphify is deliberately not exposed in the browser UI

The web upload surface (`/`, `/upload`, `/jobs/{id}`, `/jobs/{id}/download`) ends at "deliver the `.md` corpus to the user". Graphify is **not** triggered from the upload page in the current iteration and is **not** wrapped behind any HTTP endpoint of this service. Reasons:

- **Different runtime model**. Graphify's semantic extraction is LLM-driven and expects an agent host (Claude Code, or a CLI run with a Gemini API key). Embedding it inside the FastAPI process would couple the demo to an LLM provider and would either block the request for minutes or require a second job queue layer.
- **Different audience**. Browser uploaders want their `.md` output. Graph operators are a smaller, separate role and run graphify against a directory they already have on disk.
- **Different cost shape**. The convert stage runs locally and free; graphify costs LLM tokens. Surfacing it behind a single button would invite confused billing.

A recreation MAY add a graphify trigger to the browser UI as a follow-up enhancement; if it does, it must keep the existing `/upload` → `.md` zip path as the default (graphify is an opt-in extra), and it must surface graphify token costs to the user before the call is made. The handoff contract — "a directory of `.md` files" — remains unchanged; the addition is purely an additional pipeline stage invoked after the user has the zip.

---

## 9. Design decisions & rationale

This section is the project's decision record. Each subsection captures one decision that shapes the system in a way that is not obvious from the code alone, and that a recreator must understand in order to avoid silently undoing it. The decisions are presented in the order they bind the architecture: ingest source, then file-class routing, then OCR backend choice, then backend interface, then dependency loading, then batch semantics, then output layout, then format details, then pipeline boundary, then runtime and deployment posture.

A decision recorded here is binding on a faithful recreation. Reversing a decision is permitted, but only with an explicit ask from the operator and an updated revision of this section. The "Alternatives considered" bullets are not invitations to swap implementations; they are the design space the project has already walked through.

A few of these decisions emerged from a recent, large-scale refactor. The first commit on `main` carries a Google-Drive-plus-CloudConvert-plus-hosted-Mistral pipeline; the current implementation has eliminated Drive, eliminated CloudConvert, demoted Mistral from the only OCR path to one of two selectable backends, and added a local Docling default. The git log captures the trajectory file-by-file. A recreator who reads the decisions below and finds them austere should remember that they are the survivors of a deliberate scope-cut, not the choices of someone who never considered the alternatives.

### 9.1 Local filesystem input, no Google Drive

- **Decision:** `POST /convert` consumes a server-local directory path (`input_dir` form field) and walks it with `pathlib.Path.rglob`. There is no cloud-storage adapter, no upload endpoint, no signed URL handler.
- **Context:** An earlier iteration of this service read source documents from Google Drive folders using `google-api-python-client` and converted office formats via the CloudConvert API. That iteration was abandoned for two reasons. First, the current operating environment for this service does not have outbound Drive access, and provisioning a service-account with Drive scopes for every operator machine was operationally untenable. Second, the long-term posture for documents containing or adjacent to PHI is local-only processing; reaching out to a third-party storage API to fetch the bytes contradicts that posture before extraction even starts. The local-filesystem path is also dramatically simpler: no auth, no pagination, no MIME negotiation, no retry on transient Drive 5xx responses.
- **Alternatives considered:**
  - Keep the Drive endpoint as the primary ingest path. Dismissed: requires a Drive-reachable runtime and a service account per operator; reintroduces an external dependency that is no longer load-bearing for the actual workflow.
  - Add an object-storage adapter (S3, GCS, Azure Blob) alongside the local path. Dismissed: same network-egress concern as Drive for PHI workloads; adds dependency surface for a use case nobody has asked for in the current scope.
  - Require uploads via `multipart/form-data` and stream the bytes through the request body. Dismissed: corpus sizes routinely exceed sensible HTTP body limits (hundreds of files, gigabytes total); operators already have the files on disk, asking them to re-upload to localhost is a tax.
  - Watch a directory for new files and process incrementally. Dismissed: the operating model is a one-shot batch invoked by a human, not a daemon; a watcher would add lifecycle complexity for no benefit.
- **Consequences:**
  - The service trusts the local filesystem absolutely: any file readable by the FastAPI process can be ingested. There is no path sandbox beyond `Path.expanduser().resolve()`.
  - Deployment must ensure the input directory is reachable from the FastAPI process. In a container this means a bind mount or a shared volume.
  - Horizontal scaling now requires either a shared filesystem (NFS, SMB, FUSE mount of object storage) or a different ingest model entirely. The system as specified is single-host.
  - The walker's exclusion rules (dotfile-prefixed segments are skipped, see section 5) substitute for any directory-allowlist mechanism; an operator pointing the service at `$HOME` is technically supported and would walk the entire home directory.
  - The endpoint cannot be safely exposed to a network where untrusted clients can submit arbitrary `input_dir` values. See decision 9.12.
  - Errors related to ingest are local errors: `not a directory`, `permission denied`, `file not found`. There is no class of remote-side ingest error (Drive 403, quota exhausted, OAuth token expired) to handle, document, or surface. The error surface shrinks accordingly.
  - The `.gitignore` continues to list `service-account.json` even though the current code does not read it. This is intentional: it prevents an operator who is mid-experiment with a re-added Drive adapter (roadmap 11.10) from accidentally committing credentials.

### 9.2 Text-native files extracted directly, not OCR'd

- **Decision:** The walker classifies each input file by extension into one of `ocr`, `docx`, `xlsx`, `csv`, `text`, `skip`. Only the `ocr` class (PDF and image extensions) is routed through the OCR backend. The `docx`, `xlsx`, `csv`, `text` classes are processed in-process by `python-docx`, `openpyxl`, the stdlib `csv` module, and `Path.read_text` respectively. There is no conversion path that turns a DOCX into a PDF and then OCRs the PDF.
- **Context:** OCR is expensive on every axis that matters for this workload: it costs latency (seconds to minutes per page on CPU-only laptops with Docling), it costs compute (PyTorch model load, CNN inference, possibly layout-analysis transformers), and on hosted backends it costs dollars per page. Documents that already contain extractable text already paid this cost when they were authored; re-paying it by rendering DOCX to PDF and then running OCR over the rendered pixels is pure waste. Worse, the DOCX-to-PDF render step (LibreOffice, CloudConvert, etc.) introduces its own fidelity loss: heading levels collapse, footnotes drift, hyperlinks become flat text, embedded fonts get substituted. OCR over that rendered output then re-introduces typical OCR errors on top of an already-degraded source. The text-native code path bypasses both losses by reading the document's text stream directly.
- **Alternatives considered:**
  - Convert every input to PDF via LibreOffice or CloudConvert, then run OCR uniformly. Dismissed: incurs both the conversion fidelity loss and the OCR cost on documents that didn't need either; adds a second heavy dependency (LibreOffice headless, or an external API); makes the failure surface larger.
  - Use a single library that "handles everything" (e.g. Unstructured, Apache Tika). Dismissed: each library has its own opinions about output structure that we would then need to override; the per-format library set we actually use is small and well-understood.
  - Always go through OCR, but only on the PDF render of the file's first page (sampling). Dismissed: not coherent for the use case; we want the whole document, not a thumbnail.
- **Consequences:**
  - Dramatically lower wall-clock time and dollar cost on text-native corpora. A directory of 100 DOCX files completes in seconds; the same corpus rendered to PDF and OCR'd would take many minutes.
  - Some structural fidelity is lost in the text-native paths because the current extractors are conservative. DOCX heading levels are currently flattened to plain paragraphs (see roadmap item 11.2). XLSX cell merges are not preserved. CSV is rendered as pipe-delimited rows with no header detection.
  - Two code paths to maintain: the OCR backend abstraction and the per-format text extractors in `app/services/textextract.py`. Adding a new text-native format is a one-function addition; adding a new OCR backend is a one-class addition.
  - The classification is extension-based and content-blind. A file named `report.pdf` that is actually a renamed DOCX will fail in the OCR backend rather than fall through to the DOCX extractor. This is acceptable in the current scope because the operator controls the input tree.
  - The extension-to-kind mapping in `app/services/local_fs.py` is the single source of truth for routing. Adding a new format is a four-line change: add the extension to the relevant set (or create a new set), add a branch in `classify`, add a new `kind` value, and add the dispatch arm in `process_folder`. No other module participates in routing.
  - Files with no extension at all (e.g. a `Makefile`, a Unix-style `README` without `.md`) are classified `skip` and emit a `FileResult` with `reason="Unsupported extension: "` (empty extension). This is correct behavior given the routing model and should not be "fixed" by sniffing content.

### 9.3 Default OCR backend = Docling; Mistral remains selectable

- **Decision:** `OCR_BACKEND` is an environment variable with default value `docling`. The recognized values are `docling` (in-process, no document-content egress) and `mistral` (hosted Mistral OCR API, requires `MISTRAL_API_KEY`). The factory function `_make_ocr_backend()` in `app/main.py` dispatches on `settings.OCR_BACKEND.lower()` and raises `RuntimeError` on unknown values.
- **Context:** The original implementation of this service used Mistral OCR as the only OCR path. That worked while the corpus was test data, but is unsuitable for the production workload, where documents contain or sit adjacent to PHI. Sending PHI bytes to a third-party hosted API is a privacy and compliance risk that the project is explicitly trying to avoid. The default must therefore be local. Among locally-runnable OCR options on commodity hardware (laptops with no dedicated GPU), Docling is the strongest current choice: it produces good quality on clean digital PDFs, adequate quality on light scans, runs on CPU, and has a Python API that maps cleanly to the `convert(path) -> str` interface. Mistral is retained as a selectable backend because (a) it remains useful for evaluation, A/B comparison, and non-PHI workloads, and (b) removing it would require a destructive code deletion that the project is not yet ready to make.
- **Alternatives considered:**
  - Marker (Surya-based). Local, similar overall quality to Docling, possibly stronger on scanned tables. Dismissed as default because Docling's Python integration and dependency story were simpler at the time of selection; Marker remains on the roadmap (see 11.3).
  - olmOCR. Highest available quality on benefits-style documents, but requires roughly 16 GB of VRAM, which is infeasible on the laptops the operators use day to day. Dismissed as default; a future GPU-hosted variant is on the roadmap (see 11.5).
  - Tesseract. Local, mature, well-understood, but poor on tables and on documents with complex layouts. Benefits documents are dominated by tables and grids; Tesseract output is not usable downstream without heavy postprocessing. Dismissed.
  - Continue with hosted Mistral as the default and accept the egress. Dismissed on privacy grounds.
  - AWS Textract, Google Document AI, Azure Form Recognizer. All hosted, all egress documents, all dismissed for the same reason as keeping Mistral as default.
- **Consequences:**
  - Deployment can ship without any API key. A `MISTRAL_API_KEY` is required only if and exactly if `OCR_BACKEND=mistral`. The constructor for `MistralService` raises if instantiated without a key.
  - Quality is good on clean digital PDFs and adequate on light scans. Very dense or very degraded scans may produce poor output; the roadmap includes a confidence-routed dual-backend pass (11.4) to address this without changing the default.
  - The first conversion in a given process incurs a Docling model-load cold start (typically several seconds on a laptop). Subsequent conversions in the same process do not. Operators running long-lived servers absorb the cold start once; operators running short-lived CLI-style invocations pay it on every run.
  - Adding a new OCR backend is a contained change: implement a class with `convert(self, path: Path) -> str`, add a branch in `_make_ocr_backend`, document the new `OCR_BACKEND` value. No other module needs to change.
  - The factory is called once at module import (`ocr = _make_ocr_backend()` at module top level in `app/main.py`). The backend instance lives for the lifetime of the process. Changing `OCR_BACKEND` requires a process restart; there is no per-request override. This is deliberate: callers should not be able to choose which backend processes their PHI bytes by setting a form field. The operator chooses, at deploy time, and the choice is fixed for the run.
  - The unknown-value branch raises `RuntimeError(f"Unknown OCR_BACKEND: {backend}")` with the offending value in the message. This is a startup-time failure (not a per-request failure): a misconfigured `OCR_BACKEND` causes uvicorn to fail to import the app, which is the desired loud-and-early signal.

### 9.4 OCR backend interface = single method `convert(path) -> str`

- **Decision:** Every OCR backend implements one method: `convert(self, path: Path) -> str`. The argument is a filesystem path to a single input file. The return is the final markdown body for that file, ready to write to disk. There is no abstract base class, no `Protocol`, no registration decorator: the contract is a duck-typed method signature enforced by usage in `app/main.py`.
- **Context:** The set of OCR backends this project will ever wire in is small (one to three). The contract between caller and backend needs to be the absolute minimum that lets the caller iterate over files and write outputs. Any richer interface (streaming, callbacks, lifecycle hooks, batch methods) adds surface area for every future backend implementer to satisfy, with no current consumer needing the extra surface.
- **Alternatives considered:**
  - Streaming generator interface (`def convert(path) -> Iterator[str]`) for memory-bounded output. Dismissed: per-file outputs are small (megabytes at the upper end); the caller has to assemble the full body anyway before writing.
  - Callback-based interface (`def convert(path, on_page=lambda body: ...)`). Dismissed: pushes control flow inversion into every backend; complicates error handling.
  - Separate `extract` and `render` methods, with a structured intermediate representation. Dismissed: requires a stable intermediate schema that both Docling and Mistral can populate; neither library exposes a native shape that maps cleanly; the schema would be invented and immediately become a maintenance burden.
  - Batch method (`def convert_many(paths) -> dict[Path, str]`) for backends that benefit from batching. Dismissed: neither current backend benefits at the call sizes we see; can be added later for a specific backend without changing the abstraction (see 11.5).
- **Consequences:**
  - Backends own all internal concerns: page wrapping markers, table rendering, image stripping, link normalization. Whatever the backend chooses to put in the returned string is what gets written to disk.
  - Callers do not need to know which backend produced the markdown. The asymmetry between backends (e.g. Mistral wraps pages with markers, Docling does not; see 9.8) is therefore a property of the output corpus, not of the calling code.
  - Adding a backend is a one-method exercise. Removing a backend is a one-branch deletion in the factory plus a directory of files in `app/services/`.
  - There is no compile-time check that a backend conforms. A backend that returns `None` or raises on every call will fail at the first file in the batch loop, which then reports it as a failure per file (see 9.6). This is acceptable given the project's scale.
  - The interface does not pass any context (no logger, no settings handle, no progress callback). Backends that need configuration take it via their constructor (e.g. `MistralService(api_key)`); backends that need observability emit it themselves (e.g. via `print`). This keeps the per-call interface trivially mockable for the test-suite roadmap item (11.8).
  - The interface does not return metadata (page count, OCR confidence, processing time). A future backend that wants to surface metadata to a router (see roadmap 11.4) will need either a parallel richer method or a structured return type; the simple `str` return remains the public interface for everything else.

### 9.5 Lazy import of heavy backend dependencies

- **Decision:** Heavy backend dependencies are imported inside the backend's class body, not at module top level. `docling.document_converter.DocumentConverter` is imported inside `DoclingService.__init__`. `mistralai.Mistral` is imported inside the `app/services/mistral.py` module body, but the service is only constructed if the factory selects it. The factory itself imports the backend module lazily (`from .services.docling_ocr import DoclingService` inside the function body, not at file top).
- **Context:** At process startup, the factory picks exactly one OCR backend. The unused backend's dependency tree should not be paid for in import-time RAM, in startup latency, or in surfacing of import errors. Docling in particular drags in PyTorch and several large model-loading paths; importing it when the operator has chosen Mistral would be wasteful. The reverse holds when Docling is the active backend.
- **Alternatives considered:**
  - Top-level imports in every service module, accepting the startup cost. Dismissed: the cost is non-trivial (PyTorch import alone is hundreds of milliseconds and tens of megabytes of RAM) and is paid every time the FastAPI process starts.
  - Plugin registry (stevedore, entry points). Dismissed: overkill for two backends; introduces an indirection that obscures which backend is active.
  - Lazy module attributes (PEP 562 `__getattr__`). Dismissed: works, but is harder to follow than putting the import inside the constructor where it is obviously executed exactly when needed.
  - Optional-dependency install groups (`pip install bulk-mistral-converter[docling]`). Dismissed: this is not a published package; the project is operator-deployed from a single requirements file.
- **Consequences:**
  - `pip install -r requirements.txt` still installs both dependency trees. The cost saved is import-time, not install-time. Operators who never use Mistral still have `mistralai` on disk; operators who never use Docling still have `docling` and its transitive `torch` on disk. This is a deliberate tradeoff: install-time complexity (extras, optional groups) is worse than disk usage.
  - Import-time RAM and startup latency are bounded by the active backend only. Switching `OCR_BACKEND` between runs changes which dependency tree is loaded into memory.
  - Import errors in an unused backend module surface only when that backend is selected. A broken `docling` install will not prevent the Mistral path from working, which is the desired property.
  - The per-format text extractors in `app/services/textextract.py` follow the same pattern: `from docx import Document` and `from openpyxl import load_workbook` are inside the function bodies, not at module top. A corpus with no DOCX files never imports `python-docx`, even if it is installed. This is a smaller win than the OCR-backend lazy imports but is consistent and worth preserving.
  - A recreator who replaces the lazy imports with top-level imports must accept the corresponding startup cost; the test in `_make_ocr_backend` should still gate construction of the unused backend even if its module is unconditionally imported.

### 9.6 Per-file failure isolation in the batch loop

- **Decision:** The batch loop in `process_folder` wraps each file's processing in `try/except Exception`. On exception, a `FileResult` with `status="failed"` and `reason=str(exception)` is appended; the loop continues. There is no retry, no backoff, no fail-fast option.
- **Context:** A typical input tree is a directory of tens to low hundreds of files produced by a long tail of carriers, employers, and brokers. A non-trivial fraction will be malformed in some way: a PDF that is actually an HTML error page renamed `.pdf`, an `.xlsx` that openpyxl refuses to open because it is actually XLS, a `.docx` with corrupted XML. The operator's expectation is that the batch produces results for as many files as it can, with clear signals for the ones it couldn't, so they can be triaged individually. Aborting the entire batch on the first malformed file forces the operator into a quarantine-and-retry loop that is worse on every axis than just reporting the failure and moving on.
- **Alternatives considered:**
  - Fail-fast: raise the first exception out of the endpoint and return HTTP 500. Dismissed: punishes the 99 good files for the 1 bad one; throws away all the work already done in the loop.
  - Retry-with-backoff for each file. Dismissed: most failures are deterministic (malformed input, unsupported format detail) and would just consume time on the retry; transient failures are rare in a local-extraction setting.
  - Dead-letter queue: append failed files to a sidecar manifest for later reprocessing. Dismissed: the `results` list already serves that purpose and is delivered in the response.
  - Configurable mode (`fail_fast=true` form field). Dismissed: speculative configurability that no operator has asked for; can be added if the need ever materializes.
- **Consequences:**
  - Callers must inspect `results[].status` to distinguish processed, skipped, and failed entries. A 200 response from `/convert` does not mean every file was processed; it means the batch ran.
  - Silent partial success is the default. An operator who looks only at the HTTP status code and not at the `results` payload will miss failures. This is documented in the operations section but is structurally a sharp edge.
  - Debugging requires reading `reason` strings. Because `reason` is `str(exception)`, the quality of the message depends on the underlying library raising a useful exception. Some failures (e.g. Docling internal errors) produce opaque strings that require running the same file through the backend in isolation to diagnose.
  - No structured logging means there is no second source of failure information. `print()` statements emit progress to stdout but do not record full stack traces (see decision 9.12 and roadmap 11.8).
  - The `except Exception` is deliberately broad. `BaseException` (which includes `KeyboardInterrupt` and `SystemExit`) is not caught, so a Ctrl-C during a long batch still terminates the process. This is the correct behavior; the operator wants Ctrl-C to mean stop.
  - Per-file failures do not affect later files in the same batch: there is no shared mutable state between iterations beyond the `results` list itself, and a `try/except` boundary contains any partial-state leakage from the backend. A Docling model that gets into a bad state on a malformed file is, in theory, a way to break this property; in practice the libraries we use do not exhibit this failure mode.

### 9.7 Markdown output that mirrors the input tree

- **Decision:** For each non-skipped input file with relative path `rel` under `input_dir`, the output is written to `<output_dir>/<rel.with_suffix(".md")>`. Parent directories are created with `mkdir(parents=True, exist_ok=True)`. The output file is plain UTF-8 markdown. There is no manifest file, no index, no sidecar metadata.
- **Context:** The downstream consumer of stage one is `/graphify`, which expects a directory of `.md` files and uses the relative paths as document identifiers. Mirroring the input tree under the output root therefore preserves the document-identity contract for free: `<input>/carrier_a/2026/sbc.pdf` maps to `<output>/carrier_a/2026/sbc.md`, and the carrier/year structure is legible to both `/graphify` and human reviewers without any additional metadata. Markdown specifically was chosen because (a) graphify and most RAG tooling consume it natively, (b) it is plain text, diffable, and grep-able, (c) it round-trips through any text editor without loss, and (d) it is the lowest-friction format for human review.
- **Alternatives considered:**
  - Single concatenated mega-file with delimiters between documents. Dismissed: destroys per-document identity; makes incremental reprocessing impossible; breaks any downstream tool that wants to load one document at a time.
  - JSON sidecars (`.md` + `.json` per input, where `.json` carries metadata). Dismissed: no current consumer needs the sidecar; the file's path already carries the only metadata that matters (source identity); roadmap item 11.7 covers the collision-handling case where a sidecar might become useful.
  - SQLite store with one row per document. Dismissed: the filesystem already is a database with strictly less ceremony; adds a runtime dependency that downstream tools then have to learn about.
  - HTML output, preserving more structure than markdown. Dismissed: graphify doesn't want HTML; markdown is the lingua franca for LLM-consumed text.
  - Output the OCR backend's native format and let consumers convert. Dismissed: the whole point of this service is corpus unification; pushing the format choice to consumers reintroduces the heterogeneity problem.
- **Consequences:**
  - The filesystem is the database for stage one. There is no index, so locating a document by anything other than its path requires walking the output tree.
  - Collisions on the extension-replaced path take last-writer-wins. Two inputs `report.pdf` and `report.docx` in the same input directory both map to `report.md` and the second one written wins. This is acceptable in the current scope because operator-curated input trees rarely have this case, but it is flagged in the roadmap (11.7).
  - Re-running `/convert` against the same input and output overwrites prior outputs in place. There is no versioning; the output reflects the latest run.
  - The output tree is safe to delete, regenerate, copy, or move with standard filesystem tools. This is a non-trivial property for downstream tooling that wants to snapshot or version corpora.
  - The output is UTF-8 with no BOM. `write_text(body, encoding="utf-8")` is the binding call in `_write_md`. Downstream tools should decode UTF-8 strictly; producing a non-UTF-8 byte sequence in `body` is a backend bug, not an output policy.
  - A commit message in the git history (`fix .md output to .txt`) is misleading: the actual code in `_write_md` continues to call `with_suffix(".md")`, and the spec's identity section pins the output extension as `.md`. The commit message captured an intent that was either reverted or never landed in this function. A recreator must produce `.md`, not `.txt`.

### 9.8 Page-wrapping markers are Mistral-only

- **Decision:** The Mistral backend wraps each page's body with `[[START OF PAGE N]]` and `[[END OF PAGE N]]` markers, separated by blank lines, via `app/services/postprocess.py::to_wrapped_markdown`. The Docling backend emits the markdown returned by `DoclingDocument.export_to_markdown()` with no page markers added.
- **Context:** Mistral's `ocr.process` response is naturally a list of `Page` objects with per-page `markdown` and `tables` fields. Wrapping each page in markers is a trivial post-processing pass and produces a string that downstream tools (and human reviewers) can use to attribute a span back to a source page. Docling's `export_to_markdown` on a `DoclingDocument`, by contrast, returns a single flat markdown string that does not include explicit page boundaries; producing the same markers requires iterating the document's page collection and rendering each separately, which is feasible but was deferred during the initial Docling integration. The asymmetry is therefore not a design preference but an unpaid debt.
- **Alternatives considered:**
  - Drop the markers from Mistral too, for uniformity. Dismissed: page attribution is useful information that the Mistral backend gives us for free; throwing it away to match a temporarily-weaker Docling backend is the wrong direction.
  - Teach Docling to emit the same markers as part of the initial integration. Dismissed at the time on grounds of scope; flagged for completion in roadmap 11.1. The work is bounded (iterate `DoclingDocument` pages, call the per-page rendering API, wrap each result).
  - Post-process the Docling output with a heuristic page detector (e.g. detect form-feed characters or recognizable page-header patterns). Dismissed: brittle, depends on document conventions, would produce wrong attributions on documents that don't follow the convention.
  - Wrap Docling output in a single `[[START OF PAGE 1]] / [[END OF PAGE N]]` envelope to at least mark "this is one document". Dismissed: misleading, because consumers reading the markers would expect them to mean what they mean elsewhere.
- **Consequences:**
  - Downstream consumers that depend on page markers work fully only on Mistral-sourced files. Consumers must either tolerate the absence of markers on Docling-sourced files or condition their behavior on the active backend.
  - This is a known asymmetry, documented as a non-goal-for-now in section 10 and as a roadmap item in 11.1. A recreator that produces page markers in both backends from day one is acceptable; a recreator that strips them from Mistral to match Docling is not.
  - The Mistral postprocessor also inlines the per-page table HTML (resolving `[tbl-N.html](tbl-N.html)` placeholders) and drops standalone image markdown lines. This is Mistral-specific cleanup that should not be applied to Docling output, which uses different placeholder conventions.
  - Marker format details are pinned: literal double-square-brackets, the word `START` or `END`, the word `OF`, the word `PAGE`, a space, the 1-indexed page number. No leading or trailing whitespace inside the brackets. The page number is `p.index + 1` (Mistral indexes from zero internally). A recreator producing `[start_of_page_1]` or `<page n=1>` does not match the contract.

### 9.9 Two-stage pipeline: corpus build then graph build

- **Decision:** This service produces a corpus of markdown files on disk and stops. Knowledge-graph construction is performed by a separate `/graphify` tool, invoked manually by the operator after `/convert` has finished. The `/convert` response includes a `graphify_hint` string that names the exact follow-up command (`/graphify <output_dir> --obsidian --wiki`). The FastAPI process never invokes `/graphify`, never imports graph-construction code, and never depends on the graph-construction stage being installed.
- **Context:** Graphify is an LLM-driven semantic-extraction pipeline that requires an agent runtime (Claude Code or Gemini), an API key, a model selection, prompt templates, and a non-trivial amount of wall-clock time per document. Folding graphify into the `/convert` endpoint would make the endpoint LLM-coupled (a hard provider lock-in), slow (each graph build is minutes to hours), non-deterministic (LLM output varies run to run), and impossible to retry cheaply (a failed graph build would force a redo of the entire corpus build). Splitting the pipeline into two stages with a directory-on-disk handoff makes each stage independently re-runnable, independently replaceable, and independently testable. It also matches the actual operator workflow: build the corpus, eyeball a few outputs to sanity-check, then commit to the more expensive graph build.
- **Alternatives considered:**
  - Bake graphify into the endpoint, with `GEMINI_API_KEY` or equivalent required at startup. Dismissed: forces every operator to provision a graph-build API key even when they only want the markdown corpus; couples the service's deployment to the graph-build provider; turns `/convert` into a long-running, non-idempotent operation.
  - Expose a second endpoint (`POST /graphify`) on the same FastAPI service that invokes the graph builder as a background job. Dismissed: graph build is heavy enough that running it inside the same process as the corpus build is operationally awkward (memory, lifecycle, restart semantics); the two stages have genuinely different runtime profiles and belong in different processes.
  - Hand off via a queue (Redis, RabbitMQ, file-system inbox) with graphify as a long-running consumer. Dismissed: introduces a broker and a daemon for a workflow that is fundamentally manual and operator-driven.
  - Produce both the markdown corpus and the graph from `/convert` but make the graph optional via a flag. Dismissed: even with a flag, the dependency is in the codebase and the operator's mental model is more complicated than "one tool, one job".
- **Consequences:**
  - The operator runs two commands. The first builds the corpus; the second builds the graph. The `graphify_hint` in the response is the documented bridge between them.
  - The contract between stages is "a directory of markdown files at a path on disk". Any tool that can produce such a directory can substitute for stage one; any tool that can consume one can substitute for stage two.
  - Either stage can be replaced independently. Swapping the OCR backend does not affect graphify. Swapping the graph builder does not affect this service.
  - There is no end-to-end "build everything" endpoint. If the operator wants one, they wrap the two commands in a shell script.
  - The `graphify_hint` is a string, not a structured object. Downstream tooling that wants to consume it programmatically must parse the string, which is brittle. This is acceptable because the hint is intended for human eyes (the operator reading the response in Swagger UI or a terminal); machine-consumed handoff is a roadmap concern, not a current one.
  - Because the handoff is a directory, an entire alternative stage-two implementation (e.g. a different graph builder, a different RAG indexer) can be plugged in without this service noticing. The service has no opinion about what comes next; it just produces markdown.

### 9.10 Python 3.11 interpreter pin

- **Decision:** The reference deployment targets Python 3.11.x. Other 3.x versions are not officially supported; 3.10 and 3.12 are likely to work but have not been validated against the dependency set.
- **Context:** Two pinned dependencies, `docling>=2.0.0` and its transitive `torch`, gate the interpreter choice. At the time this decision was taken, PyTorch did not have prebuilt wheels for Python 3.13 or 3.14 on common platforms, and Docling's own wheels were not yet available for those interpreter versions either. Building PyTorch from source on a developer laptop is technically possible but takes hours and produces a binary that is then specific to that machine. The path of least resistance, and the path that lets `pip install -r requirements.txt` complete in seconds on a fresh interpreter, is to pin to a Python version that both libraries already ship wheels for.
- **Alternatives considered:**
  - Python 3.10. Likely works; the dependency set has wheels for it. Not chosen because 3.10 is older and the project will outlast its security-support window sooner.
  - Python 3.12. Should work; wheels exist for both `docling` and `torch`. Not chosen as the official pin only because 3.11 has been the one actually exercised end to end; promoting 3.12 to official is a small validation task, not a redesign.
  - Python 3.13 or 3.14. Not feasible at the time of writing due to wheel availability for `torch` and `docling`. Will become feasible as the ecosystem catches up.
- **Consequences:**
  - Developers on Arch Linux and macOS where the system `python3` points to 3.13 or 3.14 must explicitly install Python 3.11 (e.g. via `pyenv`, `mise`, `uv`, or the distro's versioned package) and create the venv with it. The repository does not pin via `pyproject.toml`'s `requires-python`, so the error mode for a mismatched interpreter is a pip resolution failure at install time, not a clear up-front rejection.
  - Deployment containers must pin a 3.11 base image (e.g. `python:3.11-slim`). The Dockerfile roadmap item (11.9) bakes this in.
  - CI, if added, must run on 3.11 explicitly; using "latest Python" will silently drift off the pin and start failing when wheel availability changes.
  - The pin is expected to relax to "3.11 or newer" once Docling and Torch publish wheels for the current Python release. This is not a structural decision, only a current snapshot.

### 9.11 Synchronous, blocking endpoint

- **Decision:** `POST /convert` blocks until the batch is complete and returns a single response with the full `results` list. There is no `202 Accepted + poll` flow, no SSE/WebSocket progress stream, no background queue. The endpoint handler is a synchronous `def`, not `async def`, and FastAPI executes it in a threadpool by default.
- **Context:** The expected corpus size is tens to low hundreds of files. The expected client is a developer or operator at a terminal, running a single batch and waiting for it to finish. The endpoint is not a public-facing service, not a multi-user service, and not a high-throughput service. For this shape of usage, the simplest correct implementation is also the right one: do the work, return the result.
- **Alternatives considered:**
  - `202 Accepted` plus a job ID and a `/jobs/<id>` polling endpoint. Dismissed: adds a job store (sqlite or similar), adds polling logic on the client side, adds garbage collection of completed jobs; all of this for a workflow that takes minutes at most.
  - Server-sent events (SSE) streaming progress as files complete. Dismissed: useful but the client tools the operator uses (curl, httpie, the FastAPI Swagger UI) don't render SSE well; the value is marginal at this batch size.
  - WebSocket bidirectional progress channel. Dismissed: even more client-tool friction than SSE for the same marginal value.
  - Background job queue (Celery, RQ, Dramatiq). Dismissed: introduces a broker and a worker process; complete operational overkill for a single-operator tool.
- **Consequences:**
  - HTTP clients must size their request timeouts to the longest plausible batch. A cold-start Docling backend processing a 100-PDF batch can take many minutes; default client timeouts (often 30 seconds or 1 minute) will trigger and abort the call client-side even though the server is still working. Operators should override the timeout when calling from anything other than the Swagger UI.
  - The implementation is dramatically simpler than any async or queued alternative. The entire batch loop is roughly 30 lines of straight-line code in `process_folder`.
  - Memory usage is bounded by the largest single output. There is no accumulation of state across the batch beyond the growing `results` list.
  - The endpoint cannot be made high-throughput without redesign. If batches grow to thousands of files, the synchronous model becomes untenable and the roadmap item for a streaming-progress endpoint (11.6) becomes the migration path.
  - The endpoint is declared as `def`, not `async def`, on purpose. FastAPI runs synchronous endpoint functions in a worker threadpool, which gives blocking-IO code its own thread and does not stall the event loop. A recreator who "modernizes" the signature to `async def` while keeping the body synchronous will block the event loop and degrade behavior under any concurrent request. If the body is ever rewritten with async libraries, the signature change is appropriate; until then, synchronous is correct.

### 9.12 No authentication, no rate limiting, no observability stack

- **Decision:** The service ships with no authentication, no authorization, no rate limiting, no structured logging, no metrics, no tracing. It is intended to bind to `127.0.0.1` (loopback) when launched by the operator. Trust is conferred by virtue of being on the same host as the operator.
- **Context:** This is a single-user, single-host batch tool. The operator runs it on their own machine against their own input directories. There is no second user, no untrusted client, no surface that needs to distinguish callers. Adding auth, rate limiting, or a full observability stack would not make the system more correct or more secure for its actual usage pattern; it would just make it more code to maintain.
- **Alternatives considered:**
  - API key authentication via a header (`X-API-Key`). Dismissed: single-operator tool; the key is just a constant the operator types in two places.
  - OAuth or OIDC integration. Dismissed: massively overengineered for the use case.
  - mTLS for LAN deployments. Dismissed: not the deployment shape; LAN deployment is not a current need and is flagged in roadmap (11.5) only for the OCR-worker variant.
  - Structured logging via `structlog` or `loguru`. Dismissed for now: `print()` statements already provide the progress signal the operator wants; structured logging is on the roadmap as a low-priority item.
  - Prometheus metrics and OpenTelemetry tracing. Dismissed: single-host single-operator tool; nobody is going to point Grafana at it.
  - Sentry or equivalent error reporting. Dismissed: failures are already reported per file in the response; out-of-band error reporting adds a dependency (and an egress) for no current win.
- **Consequences:**
  - The service must never be bound to a public network interface. The default `uvicorn` invocation in operator documentation should specify `--host 127.0.0.1`. Binding to `0.0.0.0` is supported by uvicorn but is the operator's explicit choice and risk.
  - Any production-style deployment (multi-operator, LAN-shared) must add an auth layer in front. A reverse proxy with HTTP basic auth is the lowest acceptable bar; mTLS or an API gateway is preferable.
  - Operational debugging relies on stdout from the `print()` calls and on the per-file `reason` strings in the response. There is no log retention, no log aggregation, no log search.
  - Performance debugging requires ad-hoc instrumentation (e.g. timing a single conversion manually). There is no built-in profiling hook.
  - The Swagger UI at `/docs` and the OpenAPI schema at `/openapi.json` are served by default. These are the only HTML-rendering surfaces in the service and exist solely to make the endpoint callable by humans. A production hardening pass would disable them; for the single-operator deployment they are kept on.

### 9.13 Browser uploads served by the same FastAPI process, no separate upload microservice

- **Decision:** the `/upload`, `/jobs/{id}`, `/jobs/{id}/download`, `/`, and `/static/*` routes live in the same `app.main` module as `/convert`. There is no separate upload service, no API gateway, no nginx in front of the application doing buffered uploads.
- **Context:** single-tenant demo with no scale pressure. The deploy surface should remain one container, one process; the upload pipeline must reuse `_make_ocr_backend()` and the per-file `process_directory` loop so that operator (`/convert`) and browser (`/upload`) callers cannot drift apart in behavior.
- **Alternatives considered:** dedicated nginx + tus-style chunked-upload service in front (overbuilt for single-tenant), splitting into convert API and upload API microservices (doubles the deploy surface).
- **Consequences:** an OOM in the OCR worker takes the UI down with it; acceptable given the stated scope. If a future iteration grows multi-tenant, the upload front-end can be split out without changing the runner contract because `services.runner.process_directory` already takes a directory and an OCR backend; the seam is in place.

### 9.14 Jobs are in-memory single-tenant state, no database

- **Decision:** `JobStore` is a `dict[str, Job]` guarded by a `threading.Lock`, scoped to one Python process. No sqlite, no Redis, no postgres.
- **Context:** the system is explicitly single-tenant (one user at a time), no PHI, hobby-grade demo. A redeploy is allowed to lose in-flight jobs; the operator-visible status (`queued | running | completed | failed`) is enough.
- **Alternatives considered:** sqlite-backed job table (would survive redeploy but adds a migration story), in-memory + on-disk JSON manifest (half-step that buys little), full job queue with Redis (Celery/RQ) (overbuilt for single-tenant).
- **Consequences:** the in-memory store is fast and correct under one writer (the FastAPI worker + the sweeper thread). The TTL sweep window (`JOB_TTL_SECONDS`, default 3600 s) bounds disk usage; mid-job restarts orphan the on-disk job directory until the next sweep tick — acceptable for the current scope.

### 9.15 Render web service plan: `standard`, with persistent disk for Docling cache

- **Decision:** the production deployment target is Render's container service on the `standard` plan (≥ 2 GB RAM), with a 1 GB persistent disk mounted at `/home/app/.cache/docling`.
- **Context:** Docling + the PyTorch CPU stack resident in memory is ~2 GB; the `starter` plan's 512 MB OOMs on the first PDF inference. Without the persistent disk, every redeploy pulls model weights (~few hundred MB) on the first request and the user sees a 60–90 s cold start.
- **Alternatives considered:** `starter` plan (OOMs), `pro` plan (only justified when concurrent jobs are expected), baking weights into the Docker image (re-runs the build every weight refresh; spec prefers external persistence), no persistent disk (every redeploy pays the cold download).
- **Consequences:** the cost floor for a public deployment is set at the `standard` plan plus a small disk. Single-tenant throughput is bounded by one CPU process; concurrent users will queue.

### 9.16 Shared batch loop via `services.runner.process_directory`

- **Decision:** the per-file batch loop was extracted from `app/main.py:process_folder` into `app/services/runner.py:process_directory(in_root, out_root, ocr, *, on_progress=None)`. Both `/convert` and `JobStore.run` call it.
- **Context:** before the refactor, the loop existed inline only in `/convert`; the upload pipeline either had to copy it (drift risk) or re-architect to push directories through `/convert` internally (added an HTTP hop). Extracting the loop was the smallest change with the largest correctness payoff.
- **Alternatives considered:** keep two copies (drift risk), make the upload pipeline `requests.post` itself to `/convert` (internal HTTP overhead, weird semantics).
- **Consequences:** there is one place where routing, dispatch, write, and per-file failure isolation live. Any future contract change to the batch loop touches one function; both endpoints get the change automatically. The optional `on_progress` callback lets the upload pipeline surface live progress without `/convert` having to care.

## 10. Non-goals & constraints

This section enumerates capabilities the system deliberately does not have. Each entry pairs the absence with a brief reason. A recreator must resist adding any of these without an explicit ask from the operator; their absence is a feature, not an oversight. Where a non-goal is paired with a roadmap item that may someday lift it, the roadmap item is named.

The collective effect of these non-goals is that the system is small enough to fit in one engineer's head. The endpoint is roughly fifty lines of orchestration over a walker, a backend, and four text extractors. The dependency tree is short. The configuration surface is two environment variables. Each non-goal below is one feature the system does not have, and one corresponding decrement in the system's complexity budget. A recreator who adds three of these features without asking will produce a different system; a recreator who adds none of them and matches the rest of the spec will produce this one.

- **No async I/O.** The endpoint handler is `def`, not `async def`. The batch loop does synchronous filesystem reads and synchronous backend calls. `aiofiles`, `httpx.AsyncClient`, and similar are not used. FastAPI executes synchronous handlers in a threadpool, which is the desired model: one request, one thread, no concurrency surprises. Reason: the workload is CPU-bound (OCR inference) or disk-bound (file reads), not network-IO-bound; async would not buy throughput and would buy debugging cost.

- **No persistent state.** There is no database, no Redis, no sqlite ledger, no on-disk job index. Each `/convert` call is fully stateless: it reads from the input directory, writes to the output directory, returns the results, and forgets. Reason: stateless is simpler, and the filesystem already serves as the canonical store for the corpus.

- **No retries.** A backend failure on one file produces a `FileResult` with `status="failed"` and the loop moves on. There is no automatic retry, no exponential backoff, no transient-vs-permanent error classification. Reason: most failures in this system are deterministic (malformed input), and operators can re-run the whole batch cheaply; building retry logic adds complexity for negligible benefit. See decision 9.6.

- **No rate limiting.** Neither incoming HTTP requests nor outgoing OCR calls are rate-limited. The Mistral backend will issue requests as fast as the batch loop produces them; the Docling backend has no network calls to rate-limit. Reason: single-operator tool; the operator's own concurrency (running one batch at a time) is the rate limit.

- **No queue, no background workers.** Batches run inline on the request thread. There is no Celery, RQ, Dramatiq, Arq, or homegrown worker pool. Reason: single-host, single-operator scale; a queue would add a broker process and a worker process for a workload that already finishes in minutes.

- **No streaming response.** The full `results` list is built in memory before any bytes are sent back. The client receives a single JSON body when the entire batch is done, not an incremental stream. Reason: matches the synchronous-endpoint decision (9.11); incremental streaming is a roadmap item (11.6).

- **No automatic graphify invocation.** The endpoint does not subprocess `/graphify`, does not import graph-construction code, and does not trigger the graph build by any other means. The operator runs `/graphify` separately. Reason: the two-stage pipeline boundary (9.9) is load-bearing for operability and provider independence.

- **No Google Drive, no S3, no GCS, no object storage.** Inputs are local filesystem paths only. The service does not authenticate to any cloud provider, does not fetch any URL, does not parse any signed URL. Reason: the local-filesystem decision (9.1) is the entire ingest model. A Drive adapter is on the roadmap (11.10) but as an additive option, not a replacement.

- **No multi-tenancy.** The service has no concept of users, organizations, or access scopes. There is no per-tenant configuration, no per-tenant output isolation, no per-tenant rate limiting. Reason: single-operator tool. Multi-tenant deployment would require a different design from the ground up, not a feature flag.

- **No PII or PHI redaction.** Markdown output is produced as-is from source documents. Any PHI, PII, or other sensitive content present in the source flows through to the output. Reason: redaction is an explicit downstream step with its own correctness requirements; conflating it with extraction would couple two independently-evolving concerns. A redaction-postprocess step is on the roadmap (11.11) as an opt-in.

- **No content classification beyond extension.** The walker classifies files by extension only. There is no MIME sniffing, no magic-byte inspection, no content-type detection. A `.pdf` that is actually HTML will be routed to the OCR backend and will fail there. Reason: extension-based routing is correct on operator-curated input trees and is much faster than per-file content inspection.

- **No structured logging.** `print()` statements emit progress to stdout. There is no log level, no log format, no log destination configuration, no JSON log output, no log aggregation hook. Reason: single-operator local tool; `print()` is sufficient for the human running the batch. A structured-logging migration is implied by the Dockerization and observability work that would accompany any multi-user deployment.

- **No metrics, no tracing, no error reporting.** The service emits no Prometheus metrics, no OpenTelemetry spans, no Sentry events. Reason: same as logging; the operator is the consumer of all signal the system produces, and the response payload is sufficient.

- **No authentication, no authorization.** The service binds to loopback by default and trusts the local operator absolutely. No API keys, no OAuth, no session cookies, no CSRF tokens. Reason: see decision 9.12.

- **No CORS configuration.** The service does not set any CORS headers because it is not intended to be called from a browser. Reason: API-only service; the Swagger UI at `/docs` is served by FastAPI itself and is same-origin.

- **No frontend.** This service is an HTTP API and a directory of markdown on disk. There is no web UI, no desktop app, no CLI wrapper bundled in the repo. The Swagger UI at `/docs` is the only human-facing surface, and it is FastAPI's default. Reason: the operator's workflow is to call the endpoint from curl, the Swagger UI, or a script; a custom frontend would be additional code with no current consumer.

- **No automated test suite committed.** A smoke-test recipe is documented in the operations section but no `pytest` configuration, fixtures, or test files exist in the repository. Reason: the system is small enough that manual smoke-testing against `test_data/` has been sufficient. A pytest target is on the roadmap (11.8).

- **No CI/CD pipeline.** There is no GitHub Actions workflow, no pre-commit hooks, no linter configuration committed. Reason: single-developer cadence; CI is added when the team scales or when the test suite is added.

- **No telemetry.** The service does not phone home, does not call any analytics endpoint, does not emit usage events to any third party. Reason: privacy posture; nothing about document content or operator behavior leaves the host without an explicit operator action.

- **No internationalization.** The service assumes UTF-8 throughout and does not localize any string. The `graphify_hint` and any error messages are English. Reason: single-language operating environment; localization is not a current need.

- **No partial-tree processing.** The walker processes the entire input directory; there is no allowlist, denylist, glob filter, or "only process files modified since X" option. The only files skipped are those in dotfile-prefixed segments and those with unsupported extensions. Reason: the operator curates the input tree before pointing the service at it; in-service filtering would duplicate `find` or shell globbing without adding capability.

- **No deduplication.** Two input files with identical content are processed twice and written twice. The service does not hash inputs, does not detect duplicates, does not skip work it has already done. Reason: no current need; deduplication on the input side is the operator's responsibility, and deduplication on the output side is graphify's responsibility.

- **No output versioning.** Re-running `/convert` overwrites prior outputs in place. There is no `<output>/runs/<timestamp>/` layout, no symlink-to-current, no diff between runs. Reason: simplicity and idempotence; operators who need history use `git` or a snapshot tool on the output directory.

- **No content-addressable storage.** Output files are named by source path, not by content hash. Reason: paths are the document-identity contract with graphify (see 9.7); content-addressable storage would break that contract.

- **No partial-file outputs.** A failed file produces no output file at all. The system does not write a partial markdown body with an error marker. Reason: partial outputs are worse than no output for downstream consumers, which would have to learn yet another error convention.

- **No request validation beyond Pydantic defaults.** The `input_dir` form field is checked only for being an existing directory (`is_dir()`); there is no allowlist of acceptable roots, no maximum tree size, no maximum file count, no maximum total bytes. Reason: single-operator trust model; the operator points the service at directories they own.

- **No symlink policy.** The walker uses `Path.rglob`, which follows symlinks by default. There is no symlink-loop detection beyond what the operating system enforces, no chroot to the input root, no refusal to descend into symlinked directories. Reason: operator-curated input trees do not contain hostile symlinks; defending against this is solving a problem the system does not have.

- **No file-size guardrail.** A 4 GB PDF will be passed to the OCR backend; whether it processes successfully is the backend's concern. The service does not pre-check sizes, does not refuse oversized inputs, does not stream-decode. Reason: operator knows their corpus; size limits are speculative without operator input.

- **No partial-batch resumption.** A batch that is killed midway leaves the output directory in whatever state it reached at that moment. There is no resume token, no `--continue-from` flag, no marker file indicating "processing in progress". Reason: idempotence (re-running produces the same outputs) makes resumption unnecessary: the operator just runs `/convert` again and any successfully written files are simply overwritten with the same content.

- **No concurrent backend calls.** Within a single batch, the OCR backend is called serially, one file at a time. There is no thread pool, no asyncio gather, no multiprocessing. Reason: the backends (Docling especially) are not thread-safe in any guaranteed way, and serial processing keeps memory usage and progress reporting trivial.

- **No request-ID propagation, no correlation IDs.** Each `/convert` call is anonymous from a tracing perspective. There is no `X-Request-ID` header, no log correlation key, no trace propagation. Reason: single-operator tool with no upstream caller to correlate with.

- **No HTTPS termination in the process.** The service speaks plaintext HTTP. TLS termination is the responsibility of the deployment platform (Render terminates on `*.onrender.com` automatically) or a reverse proxy in any self-hosted scenario.

The following non-goals are specific to the upload pipeline added in section 3.5–3.7 and the web deployment added in section 5.11:

- **No user accounts, no login, no per-user state.** The deployed web surface is single-tenant. There is no `users` table, no session cookie, no JWT. Reason: hobby demo scope; adding accounts is a separate decision, not a missed checkbox.
- **No file-content authentication.** Anyone who reaches the `/upload` URL can submit files; the only abuse mitigations are the `MAX_UPLOAD_BYTES`, `MAX_FILES_PER_JOB`, and `UPLOAD_RATE_LIMIT` knobs. Reason: matches scope; any non-demo deployment must add an auth layer in front (API key middleware is the minimum).
- **No durable jobs.** A redeploy or process restart drops every in-flight job and its on-disk inputs/outputs (see decision 9.14). The `JobStore` is in-memory only. Reason: single-tenant, no PHI, demo-grade.
- **No per-file real-time progress events.** The polling client sees per-file results appended to the `results` list of `GET /jobs/{id}`, but the granularity is "result appears when its file finishes". There is no streaming SSE, no per-page progress, no WebSocket. Reason: 2-second polling is sufficient for the corpus sizes the demo will see; full streaming is a roadmap item.
- **No anti-virus or content scanning.** Uploaded files are persisted to disk and read by the OCR backend without inspection. Reason: scope-bound to a non-PHI demo and a trusted CPU sandbox; any PHI or multi-tenant production deployment must add scanning.
- **No PII redaction on outputs.** Whatever PHI or PII is present in source documents flows through to the markdown outputs. Reason: scope-bound; redaction is a roadmap item flagged for any future PHI-aware deployment.
- **Graphify is not wired into the browser UI.** The browser path ends at "deliver the `.md` zip"; running graphify is an operator-side step. Reason: decision 8.10.

## 11. Roadmap & extension hooks

This section catalogs the planned next steps and the points in the code where they would be added. Each item is presented with three fields: **What** (the capability), **Why** (the operator need it serves), and **How to add (entry points)** (the specific files and functions a contributor would touch). Items are ordered by approximate priority, with the most-asked-for items first; ordering is not a commitment.

A roadmap item is a sanctioned extension. A change that matches one of these descriptions is a smaller proposal than a change that introduces a wholly new capability. Conversely, a change that does not appear here is, by default, out of scope; recreators should add roadmap items here before adding capability there.

The roadmap is not a backlog. It is a list of extensions that have been considered, are coherent with the architecture, and would be welcome additions if and when the operator asks for them. Each item leaves the existing architecture intact: the backend interface stays single-method, the endpoint stays directory-in directory-out, the dependency between stages stays a filesystem path. An extension that violates one of those properties is not on the roadmap; it is a redesign and belongs in a different document.

### 11.0 Recently promoted from roadmap (implemented)

The following items were on previous editions of this roadmap and now exist in the codebase. They are listed here so a reader of the roadmap can quickly orient on what is "still pending" versus "already shipped":

- **OCI / Docker packaging.** `Dockerfile` and `.dockerignore` ship at the repo root; a multi-stage build produces a single image used by the Render deployment.
- **Streaming progress (partial).** `POST /upload` returns immediately with a job id; `GET /jobs/{id}` exposes per-file `FileResult` records as they accumulate. The polling cadence is 2 seconds in the bundled UI. A full Server-Sent-Events or WebSocket streaming surface remains in the roadmap (`11.6`).
- **Web deployment target.** A `render.yaml` blueprint targets Render's container service. See §5.11 for the full deployment spec.
- **Public-surface hardening.** `slowapi` rate limiting, `MAX_UPLOAD_BYTES` and `MAX_FILES_PER_JOB` caps, filename sanitization, path-traversal guards, and `logging` (replacing the previous `print()` calls) all landed alongside the upload pipeline.

### 11.1 Docling page markers

- **What:** Emit `[[START OF PAGE N]]` and `[[END OF PAGE N]]` markers from the Docling backend, matching the format the Mistral backend already produces. The goal is per-page attribution parity across the two OCR backends so downstream tools can rely on the markers regardless of which backend produced a given file.
- **Why:** Per-page attribution is useful for human reviewers (locate the source page given a markdown span), for graphify (associate extracted entities with their source page), and for retrieval (chunk at page boundaries). The current asymmetry (decision 9.8) forces downstream code to special-case the Docling-sourced files. Closing the gap removes that special case.
- **How to add (entry points):** In `app/services/docling_ocr.py::DoclingService.convert`, iterate the pages of the `DoclingDocument` returned by the converter rather than calling `export_to_markdown` once on the whole document. For each page, render its markdown via Docling's per-page API (or the equivalent slice of the document), wrap it in the same marker format used by `app/services/postprocess.py::to_wrapped_markdown`, and join with blank lines. Consider extracting the wrapping logic into a shared helper in `postprocess.py` so both backends use the same marker format definition.
- **Risks:** Docling's per-page rendering may differ from its whole-document rendering in subtle ways (heading promotion, list continuation across pages, table-split handling). A test fixture that compares per-page-stitched output against current whole-document output is recommended before this change lands.
- **Follow-on work:** Once page markers are emitted, downstream consumers can rely on them; document that reliance in section 7 of the full spec (output format) so it becomes a contract rather than an accident.

### 11.2 DOCX heading-level preservation

- **What:** Preserve heading levels in DOCX extraction so that `Heading 1` styles become `# ...`, `Heading 2` become `## ...`, and so on, up to `######` for `Heading 6`. Currently all paragraphs are emitted at body level with blank-line separation, which flattens document structure.
- **Why:** Heading structure is load-bearing for graphify and for human review. Plan summaries and SBC documents rely on heading hierarchy to delineate sections (plan-level, coverage-category-level, benefit-level), and that hierarchy is lost in the current extraction.
- **How to add (entry points):** In `app/services/textextract.py::extract_docx`, inspect `paragraph.style.name` for each paragraph. Map the style name to a markdown heading level (e.g. `Heading 1` to `# `, `Heading 2` to `## `, falling back to body text for unknown or `Normal` styles). Emit the prefixed line in place of the raw text. Preserve existing behavior for tables. Optionally also handle list paragraphs (`List Paragraph`, `List Bullet`, etc.) by emitting `-` or `1.` prefixes.
- **Risks:** Style names in DOCX are author-defined; documents that use custom styles will fall through to the body-text branch. The mapping should be additive (more styles recognized over time) and should never raise on an unknown style.
- **Follow-on work:** Apply the same hierarchy-preservation principle to XLSX (sheet titles and merged header rows) and to OCR backends that surface heading information (Docling does, Mistral surfaces it implicitly via font-size cues).

### 11.3 Marker fallback backend

- **What:** Add a third OCR backend, `marker`, that wraps the Marker library (Surya-based local OCR with strong table handling) and is selectable via `OCR_BACKEND=marker`. Useful when running on a host with more compute than a laptop but still local-only.
- **Why:** Marker outperforms Docling on some scanned-table documents and is a reasonable middle ground between Docling (CPU-friendly, decent quality) and olmOCR (GPU-required, top quality). Having it available as a backend lets operators pick the right tradeoff per deployment.
- **How to add (entry points):** Create `app/services/marker_ocr.py` with a `MarkerService` class implementing `convert(self, path: Path) -> str`. Import the Marker library lazily inside the class constructor (per decision 9.5). Register the backend in `app/main.py::_make_ocr_backend` with a new branch on `backend == "marker"`. Add `marker-pdf` or the equivalent package to `requirements.txt`, document the new `OCR_BACKEND` value in `.env.example`, and confirm it appears in the health check output.
- **Risks:** Marker pulls in its own Torch-using model stack, increasing total install size. A recreator should validate that having both Docling and Marker installed at the same time does not produce conflicting PyTorch wheel constraints; if it does, the dependency lock needs care.
- **Follow-on work:** Once Marker is wired in, add a small benchmarking script under `scripts/` that runs each backend against the same `test_data/` corpus and produces a comparison report. This makes the per-backend tradeoff visible to operators.

### 11.4 Confidence-routed dual-backend pass

- **What:** Run Docling first on every page; for pages whose OCR confidence falls below a configurable threshold, re-run those specific pages through a heavier backend (Marker locally, or a remote olmOCR endpoint). Merge the page outputs into a single markdown body. This is a quality-on-demand strategy that pays the cost of the heavy backend only on pages that need it.
- **Why:** Most pages in a benefits corpus are clean digital PDFs that Docling handles correctly. A minority (scanned faxes, handwritten amendments, poorly-photocopied addenda) need a stronger model. Running the stronger model on every page is wasteful; running the weaker model on every page costs quality on the hard pages. Confidence routing gets both.
- **How to add (entry points):** Extend the Docling backend to expose per-page confidence scores (Docling's underlying models provide these; surface them on a per-page basis from `DoclingService.convert`, possibly via an internal richer return type). Add a router in `app/services/router.py` (new module) or in `app/main.py` that consumes the per-page confidence, decides which pages to re-process, calls the heavier backend on those pages, and stitches the result. The router itself is a new OCR-backend implementation from the caller's perspective; it implements `convert(path) -> str` and internally orchestrates two backends.
- **Risks:** The confidence threshold is a tuning parameter with no obvious default. Setting it too low routes nothing to the heavy backend (quality wins lost); setting it too high routes everything (cost savings lost). The threshold should be configurable per deployment via an env var, with a sensible empirical default chosen after benchmarking.
- **Follow-on work:** Once routing exists, the `FileResult` model could be extended with a `pages_reprocessed: int` field to surface routing decisions back to the operator. This is a small, additive schema change that helps operators understand cost.

### 11.5 Remote OCR worker

- **What:** Split the OCR step from the rest of the service so that a laptop client running `/convert` can offload OCR to a GPU server on the LAN. The local FastAPI process still walks the input tree, classifies files, and writes outputs; for each `ocr` file it POSTs the bytes to a remote inference endpoint and receives the markdown body in response.
- **Why:** Operators on laptops are CPU-bound and slow on dense scanned corpora. A small LAN-local GPU box (a single workstation) can run olmOCR or a quantized variant and serve the whole team. This preserves the privacy posture (LAN-only, not internet) while removing the laptop's CPU bottleneck.
- **How to add (entry points):** Implement `app/services/remote_ocr.py::RemoteOCRService` with `convert(self, path: Path) -> str` that reads the file bytes, POSTs them to a configured endpoint (`REMOTE_OCR_URL` env var), and returns the response body as markdown. Add the new env var to `app/config.py::Settings`. Register the backend in `_make_ocr_backend` with `backend == "remote"`. The remote endpoint's API contract (request schema, response schema, auth) is a separate spec for the worker service; the client side only needs to POST bytes and receive markdown.

### 11.6 Streaming progress endpoint

- **What:** Replace or supplement the synchronous `/convert` with a 202-Accepted-plus-polling pattern, or with server-sent events streaming per-file completion. The client receives progress as the batch runs rather than waiting for the whole batch to finish.
- **Why:** For large batches (hundreds of files) the synchronous response can take many minutes, during which the client has no signal that work is being done. Progress feedback improves operator experience and avoids client-side timeout misconfiguration (decision 9.11).
- **How to add (entry points):** Introduce a minimal job store; sqlite in the output directory is sufficient and matches the no-external-dependency posture. Add `POST /convert` returning `202 Accepted` with a `job_id`, a `GET /jobs/<id>` returning current status and partial results, and optionally a `GET /jobs/<id>/events` SSE endpoint. The batch loop moves into a background task (FastAPI's `BackgroundTasks` or a threadpool job). The synchronous endpoint can be retained as `/convert/sync` for backwards compatibility, or replaced outright with a documentation update.

### 11.7 Output-path collision handling

- **What:** When two inputs map to the same `.md` output path (e.g. `report.pdf` and `report.docx` in the same input directory), detect the collision and disambiguate. Options: suffix the later output with a short hash, fail the second write with a clear error in the file's `FileResult`, or merge the bodies under a section header.
- **Why:** The current last-writer-wins behavior (decision 9.7) silently loses data. Even one collision in a batch is a correctness bug from the operator's perspective; detection plus a clear signal in the response is the minimum acceptable improvement.
- **How to add (entry points):** Add a collision check in `app/main.py::_write_md` before writing: maintain a set of written paths in `process_folder` (passed through or held on a small context object), check membership before `write_text`, and choose a disambiguation strategy. The simplest policy: append `_<source-ext>` to the stem (`report_pdf.md` and `report_docx.md`); the strictest policy: refuse the second write and emit `status="failed"` with `reason="output path collision with <other input>"`.

### 11.8 Automated test suite

- **What:** A pytest-based test suite covering the walker (extension classification, dotfile exclusion, sorted order), the per-format extractors (DOCX/XLSX/CSV/text against small fixture files), the backend factory (correct dispatch on `OCR_BACKEND`, error on unknown value), and the endpoint (correct `FileResult` shape, correct `output_path` mirroring, correct failure isolation). The OCR backends should be stubbed with a fixed-string returner so the suite runs without model downloads or API keys.
- **Why:** The system is small but the consequences of regressions are corpus-wide. A test suite makes future refactors safe and documents the contract more precisely than prose.
- **How to add (entry points):** Create a `tests/` directory at repo root. Add `tests/conftest.py` with a `tmp_path`-based fixture that builds a tiny input tree (one DOCX, one CSV, one PDF stub). Add `tests/test_walker.py`, `tests/test_textextract.py`, `tests/test_endpoint.py`. Override `OCR_BACKEND` to a `tests/stub_backend.py` implementation by monkeypatching `_make_ocr_backend` or by setting an env var before app import. Wire pytest into the requirements (dev-only group eventually; for now a top-level `pytest` pin in a `requirements-dev.txt`).

### 11.9 OCI/Docker packaging

- **What:** A `Dockerfile` at repo root that produces a slim runtime image with Python 3.11, the pinned dependencies, and the app code. Multi-stage build to keep the final image size manageable given that the dependency closure includes PyTorch (which is large).
- **Why:** Operators who don't want to manage a Python 3.11 install on their host (decision 9.10) can run the service in a container. Deployment to a LAN-shared host (e.g. the GPU box for the remote-OCR-worker variant) is significantly easier as a container than as a host install.
- **How to add (entry points):** Create `Dockerfile` at repo root. Use `python:3.11-slim` as the base. Stage one: install build dependencies, create a venv, `pip install -r requirements.txt`. Stage two: copy the venv and the `app/` directory into a fresh `python:3.11-slim`. Set `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`. Add `.dockerignore` excluding `out/`, `graphify-out/`, `test_data/`, `__pycache__/`, `.git/`, `.venv/`. Note that binding to `0.0.0.0` inside the container is correct; the network exposure is then controlled by the container runtime's port-publishing rules. The auth caveat from decision 9.12 applies: if the published port is reachable from anything other than the operator's host, an auth layer must front it.

### 11.10 Reintroduce Google Drive ingestion as an optional adapter

- **What:** Re-add a Google Drive ingestion path as an opt-in adapter alongside the local-filesystem path. When the `input_dir` form field is a Drive folder URL (or when a new `input_url` field is present), the service downloads the Drive folder to a temporary local directory and then runs the existing pipeline against the temp directory. Local-filesystem ingest remains the default.
- **Why:** Some operator workflows are still rooted in Drive (shared folders from carriers, broker-prepared exports). Forcing those operators to manually download to a local directory before invoking `/convert` is friction. An opt-in Drive adapter restores the convenience without changing the default.
- **How to add (entry points):** Create `app/services/drive.py` (new) implementing a `fetch(folder_url, dest_dir)` function that authenticates to Drive via a service account, recursively downloads files into `dest_dir`, and returns the resolved local path. Add `GOOGLE_SERVICE_ACCOUNT_JSON` and `DRIVE_ENABLE` env vars to `app/config.py`. In `app/main.py::process_folder`, detect whether `input_dir` is a Drive URL (by scheme prefix) and, if so, fetch into a `tempfile.mkdtemp()` location before calling `walk`. The rest of the pipeline is unchanged. Document the new ingest mode in `.env.example`.

### 11.11 PHI redaction step

- **What:** An opt-in postprocess that runs a redaction pass over each markdown body before write. Implementation could be Microsoft Presidio (broad PHI/PII detector), a custom regex pass for known sensitive patterns (member IDs, dates of birth, SSNs), or a combination. Enabled by a `REDACT_PHI=true` env var.
- **Why:** The current system flows source PHI through to the output verbatim (non-goal in section 10). For some downstream consumers (a shared corpus, an LLM-as-a-service workflow, a model training set) the operator wants PHI removed at the corpus-build step. An opt-in redactor handles that without changing the default behavior.
- **How to add (entry points):** Create `app/services/redact.py` exposing a `redact(body: str) -> str` function. Add `REDACT_PHI: bool = False` to `app/config.py::Settings`. In `app/main.py::process_folder`, after the per-file `body = ...` extraction and before `_write_md`, conditionally call `redact(body)` when `settings.REDACT_PHI` is true. The redactor's policy (what to detect, what to replace with) is documented in a sub-section of the operations doc; the default replacement is `[REDACTED]` for each match.

### 11.12 Cross-cutting: configuration via `pyproject.toml`

- **What:** Migrate the dependency declaration and Python-version pin from `requirements.txt` to a `pyproject.toml` with PEP 621 metadata, and add a `[project.requires-python]` field that fails `pip install` cleanly on a mismatched interpreter.
- **Why:** Currently a wrong-Python install fails with a confusing resolver error. `requires-python` gives a clear up-front signal. `pyproject.toml` also enables modern packaging workflows (`uv`, `pdm`, build isolation).
- **How to add (entry points):** Create `pyproject.toml` at repo root with `[project]` metadata, `dependencies = [...]` copied from `requirements.txt`, and `requires-python = ">=3.11,<3.12"`. Keep `requirements.txt` generated from `pyproject.toml` for compatibility (`pip-compile` or `uv pip compile`). Update operator documentation to recommend `pip install -e .` over `pip install -r requirements.txt`.

### 11.13 Per-file mid-loop progress updates

- **What:** Surface progress at the granularity of individual pages or backend phases, not just per-file completion. Today `process_directory`'s `on_progress` callback fires once per file at end-of-file; the polling client therefore sees a file appear as either "absent" or "done" with no intermediate state.
- **Why:** With per-file durations measured at ~8-10 s on the docling backend (see `BENCHMARK.md`), a 50-file batch takes ~7 minutes. Users staring at the upload UI for that long with no per-file feedback experience the system as hung. Per-page progress would give a continuous signal.
- **How to add (entry points):** Extend the OCR backend interface from `convert(path) -> str` to `convert(path, on_page=...) -> str` where `on_page` is an optional callback fired after each page. Pipe it from `runner.process_directory` to the existing `on_progress` callback. Update `JobStore.run` to mutate a per-file progress field on each call. Update `JobStatusResponse` to expose that field.
- **Risks:** Increases the surface area of the backend protocol. Backends that cannot easily emit per-page progress (Mistral's hosted call is atomic) would have to no-op the callback, which is fine.

### 11.14 Job persistence in sqlite

- **What:** Replace the in-memory `JobStore` with a sqlite-backed store so that jobs survive process restarts and redeploys.
- **Why:** A Render redeploy currently wipes in-flight jobs (decision 9.14). For a hobby demo this is acceptable; for any deployment with longer batches or users in different time zones, losing a job to a 5-minute redeploy is a poor experience.
- **How to add (entry points):** Introduce `app/services/db.py` exposing a thin sqlite wrapper. Persist `Job` records on every state transition. On startup, scan for `running` jobs and either re-run them or mark them `failed` with an explanatory `error` string. The on-disk job directory tree under `${DATA_DIR}/jobs/<id>/` already survives restarts; only the in-memory index needs durability.

### 11.15 Graphify trigger in the browser UI

- **What:** After a job completes, expose a button "Build knowledge graph" that, when clicked, kicks off a graphify run against the job's `out/` directory and surfaces the resulting `GRAPH_REPORT.md` + an obsidian zip back to the user.
- **Why:** Operators who want the graph today have to drop to a Claude Code session. A browser button would close the workflow loop for users who want both stages.
- **How to add (entry points):** A new endpoint `POST /jobs/{id}/graphify` that creates a "graph job" attached to the existing job's `out_dir`. Either invokes the graphify CLI as a subprocess (requires `GEMINI_API_KEY` on the host) or POSTs to a graphify-hosted service. Streams progress through a second polling endpoint. Surfaces `graphify-out/` artifacts via a second zip download. Costs should be shown to the user before the call is made (graphify's per-run token cost is non-zero).
- **Risks:** Couples the deployment to an LLM provider; conflicts with the privacy-first stance for any PHI deployment. Must be opt-in and clearly labeled.

### 11.16 API-key middleware for non-demo deployments

- **What:** A `Depends(require_api_key)` middleware on every non-`/health` route that checks an `X-API-Key` header against a value supplied via `API_KEY` env var; rejects with 401 if absent or mismatched.
- **Why:** Any deployment that grows beyond "hobby demo" needs at least one auth layer. API-key middleware is the simplest credible step up.
- **How to add (entry points):** New module `app/middleware/auth.py` exporting `require_api_key`. Apply via `Depends` on `/upload`, `/jobs/{id}`, `/jobs/{id}/download`, `/convert`, `/`. Leave `/health` unauthenticated for load-balancer probes. Add `API_KEY` to `app/config.py` and `.env.example` with a clear "leave empty to disable auth (default)" comment.
- **Risks:** Backwards-incompatible if any caller already exists; gate the check on a non-empty `API_KEY` setting so the demo deployment continues to work without changes.

```text
Files of record
app/main.py
app/config.py
app/models.py
app/services/local_fs.py
app/services/docling_ocr.py
app/services/mistral.py
app/services/textextract.py
app/services/postprocess.py
requirements.txt
.env.example
.gitignore
git log (refactor trajectory from Drive + CloudConvert + Mistral to local FS + text extractors + Docling default)
```

---

## Appendix A. File inventory

Every source path the project ships, repo-relative, one line per file, with a one-sentence role description. Generated directories (`.venv/`, `out/`, `test_data/`, `graphify-out/`, `spec_parts/`, `__pycache__/`) and editor/OS artifacts (`.DS_Store`) are not source and are omitted.

| Path                              | Role                                                                                                                                  |
|-----------------------------------|---------------------------------------------------------------------------------------------------------------------------------------|
| `requirements.txt`                | Pinned Python runtime dependencies installed via `pip install -r requirements.txt`.                                                   |
| `.env.example`                    | Canonical template for the runtime `.env` file; lists the two environment variables (`OCR_BACKEND`, `MISTRAL_API_KEY`).               |
| `.gitignore`                      | Minimal ignore list — currently only `.env` and `service-account.json`.                                                               |
| `app/main.py`                     | FastAPI application entry point: defines `app`, `/convert`, `/health`, and constructs the OCR backend at import time.                  |
| `app/config.py`                   | `pydantic-settings`-based typed loader for `OCR_BACKEND` and `MISTRAL_API_KEY`; exposes a module-level `settings` singleton.          |
| `app/models.py`                   | Pydantic response schemas (`FileResult`, `ProcessFolderResponse`) shared by the API layer.                                            |
| `app/services/local_fs.py`        | Filesystem walker that classifies each input file into a `kind` (`text`, `csv`, `docx`, `xlsx`, `ocr`, `skip`) and yields metadata.    |
| `app/services/textextract.py`     | Deterministic extractors for non-OCR formats: `extract_text`, `extract_csv`, `extract_docx`, `extract_xlsx`.                          |
| `app/services/docling_ocr.py`     | `DoclingService` wrapper around `docling.document_converter.DocumentConverter`; converts PDFs to markdown via local CPU inference.    |
| `app/services/mistral.py`         | `MistralService` wrapper around the `mistralai` SDK; converts PDFs to markdown via the hosted Mistral OCR API.                        |
| `app/services/postprocess.py`     | Shared text-normalization helpers applied to extractor output before it is written to disk.                                           |
| `app/services/runner.py`          | Shared per-file batch loop (`process_directory`); called by both `/convert` and the upload-job runner.                                |
| `app/services/jobs.py`            | `Job` dataclass, `JobStore`, background runner, TTL sweeper, filename sanitization for the upload pipeline.                            |
| `app/services/zip_out.py`         | `zip_directory(src, dest)` writes a DEFLATE zip of the per-job `out/` directory for the download endpoint.                            |
| `app/templates/index.html`        | Jinja2 template rendering the drag-and-drop upload UI served at `/`.                                                                  |
| `app/static/style.css`            | Minimal stylesheet for the upload UI; system font; no frameworks.                                                                     |
| `app/static/app.js`               | Vanilla JS that drives upload, polls `/jobs/{id}` every 2 seconds, renders results, surfaces the download link.                       |
| `Dockerfile`                      | Multi-stage container build (Python 3.11-slim) for the Render web deployment.                                                          |
| `.dockerignore`                   | Excludes generated directories (`.venv/`, `bench/`, `out/`, `test_data/`, `graphify-out/`, `spec_parts/`) and large docs from the image. |
| `render.yaml`                     | Render Infrastructure-as-Code: web service definition, persistent disk mount for Docling cache, environment variable contract.        |
| `bench/gen_corpus.py`             | Developer-only utility that synthesizes 150 PDFs of varied complexity into `bench/corpus/` for the benchmark harness.                  |
| `bench/run_bench.py`              | Benchmark harness: runs the chosen OCR backend in-process over the corpus, captures per-file timing, persists `bench/results.json`.    |
| `bench/render.py`                 | Renders `bench/results.json` into `BENCHMARK.md` with throughput, per-kind, and warm-vs-cold tables.                                   |
| `scripts/gen_test_data.py`        | Developer-only utility that synthesizes six representative fixtures into `./test_data/`; requires `reportlab` in addition to the runtime dependencies. |

## Appendix B. Pinned dependency list

The complete and authoritative contents of `requirements.txt`, reproduced verbatim:

```text
fastapi==0.115.6
uvicorn[standard]==0.30.6
python-dotenv==1.0.1

pydantic==2.10.6
pydantic-settings==2.7.1

mistralai>=1.9.0
python-multipart==0.0.9

python-docx==1.1.2
openpyxl==3.1.5

docling>=2.0.0

jinja2==3.1.6
slowapi==0.1.9
```

Rationale for each top-level pin:

| Package              | Version pin       | Why it is in the project                                                                                                                            |
|----------------------|-------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| `fastapi`            | `==0.115.6`       | HTTP framework hosting the `/convert` and `/health` endpoints.                                                                                       |
| `uvicorn[standard]`  | `==0.30.6`        | ASGI server used to run the FastAPI app; the `[standard]` extra brings in `httptools` and `uvloop` for production-grade performance.                |
| `python-dotenv`      | `==1.0.1`         | Loads `.env` into `os.environ` at startup (`app/main.py` calls `load_dotenv()` as its first statement).                                              |
| `pydantic`           | `==2.10.6`        | Underlying validation library for request/response models and the typed settings loader.                                                            |
| `pydantic-settings`  | `==2.7.1`         | Typed env-var loader (`app/config.py:Settings`); reads `OCR_BACKEND` and `MISTRAL_API_KEY` with defaults and validation.                            |
| `mistralai`          | `>=1.9.0`         | Official SDK for the Mistral OCR API; used only by `app/services/mistral.py` when `OCR_BACKEND=mistral`. Floor pin (not exact) because the API surface used is stable and recent. |
| `python-multipart`   | `==0.0.9`         | Required by FastAPI to parse `multipart/form-data` request bodies — `/convert` accepts form fields, not JSON.                                       |
| `python-docx`        | `==1.1.2`         | DOCX paragraph and table extraction; powers `extract_docx` in `app/services/textextract.py`.                                                        |
| `openpyxl`           | `==3.1.5`         | XLSX sheet iteration and cell reads; powers `extract_xlsx` in `app/services/textextract.py`.                                                        |
| `docling`            | `>=2.0.0`         | Local OCR backend; provides `DocumentConverter` which is wrapped by `app/services/docling_ocr.py`. Floor pin (not exact) because the project tracks the 2.x line.        |
| `jinja2`             | `==3.1.6`         | Server-side template renderer for the upload UI (`app/templates/index.html`); FastAPI's `Jinja2Templates` integration uses it directly.                |
| `slowapi`            | `==0.1.9`         | IP-keyed rate limiting on `POST /upload` (`UPLOAD_RATE_LIMIT` env, default `10/minute`); wraps starlette middleware.                                                  |

Note on `reportlab`: `reportlab` is **intentionally absent from `requirements.txt`**. It is required only by `scripts/gen_test_data.py` to synthesize the `sbc_excerpt.pdf` fixture, which is a developer-only artifact. A production install of this service does not need `reportlab`. A recreation that omits the test-fixture generator may omit `reportlab` entirely; a recreation that includes the generator should install `reportlab` separately as documented in section 5.4.

Transitive dependencies (e.g. `torch`, `transformers`, `easyocr`, `rapidocr`, `huggingface-hub`, `numpy`, `pillow`, `starlette`, `anyio`) are pulled in by the pins above and are not themselves listed because the pin set above is what `pip` is told to install; their resolved versions are whatever the resolver picks at install time. If reproducibility of transitive versions becomes important, the project would graduate to a lockfile (`pip-tools`, `uv`, or `poetry`); that is out of scope today.

```text
Files of record
- requirements.txt
- .env.example
- .gitignore
- app/main.py
- app/config.py
- app/models.py
- app/services/local_fs.py
- app/services/textextract.py
- app/services/docling_ocr.py
- app/services/mistral.py
- app/services/postprocess.py
- scripts/gen_test_data.py
- test_data/intro.txt
- test_data/policy.md
- test_data/benefits.csv
- test_data/plan_summary.docx
- test_data/rates.xlsx
- test_data/sbc_excerpt.pdf
- out/intro.md
- out/policy.md
- out/benefits.md
- out/plan_summary.md
- out/rates.md
- out/sbc_excerpt.md
```

---

## Appendix C. Reference test-fixture outputs

The six files below are the verbatim outputs produced by a successful smoke-test run of `/convert` against the fixtures in `./test_data/`. A recreator should be able to diff their own conversion output against these to confirm fidelity.

For text-kind, csv-kind, xlsx-kind, and docx-kind outputs, byte equality is the contract — a correct implementation produces exactly these bytes.

For OCR outputs, exact byte equality is **not guaranteed** across model weight versions, library versions, or backend choices. The structural shape (heading hierarchy, table cell ordering, paragraph flow, presence of page markers for the mistral backend) is the contract. The snapshot below is from the docling backend; a mistral-backend run of the same fixture would carry `[[START OF PAGE N]]` markers and HTML tables instead of GFM tables.

**Recommended diff harness for recreators** (informative):

```
# byte-equality check for deterministic outputs
for f in intro.md policy.md benefits.md plan_summary.md rates.md; do
  diff -q ./out_test/$f ./out/$f || echo "MISMATCH: $f"
done

# structural check for OCR output (docling)
grep -q '^## '       ./out_test/sbc_excerpt.md || echo "OCR: missing heading"
grep -q '^|---'      ./out_test/sbc_excerpt.md || echo "OCR: missing table separator"
grep -q 'START OF PAGE' ./out_test/sbc_excerpt.md && echo "OCR: unexpected page marker (docling should not emit)"
```

A recreator MAY tighten this harness for their own CI but MUST NOT require byte equality on OCR outputs as an acceptance gate.

### C.1 `out/intro.md`

Produced from `test_data/intro.txt` by the `text` kind (passthrough, no backend).

```
Welcome to Acme Health Plan.

Members may visit any in-network provider without a referral.
Emergency room visits are covered worldwide.
```

### C.2 `out/policy.md`

Produced from `test_data/policy.md` by the `text` kind (passthrough, no backend). Output is byte-identical to the input file.

```
# Policy Notes

- Deductible resets January 1.
- Out-of-pocket max is $6,000 individual / $12,000 family.
- Telehealth visits have $0 copay through Q4 2026.
```

### C.3 `out/benefits.md`

Produced from `test_data/benefits.csv` by the `csv` kind (no backend).

```
service | in_network_copay | out_of_network_copay
primary_care | 25 | 80
specialist | 50 | 150
urgent_care | 40 | 120
emergency_room | 300 | 300
```

### C.4 `out/plan_summary.md`

Produced from `test_data/plan_summary.docx` by the `docx` kind (no backend). Note the blank-line separation between every paragraph and every table row, which is a direct consequence of the `"\n\n".join(parts)` builder in `extract_docx`.

```
2026 Plan Summary

This document summarizes covered benefits for the Acme PPO Gold plan.

Preventive Care

Annual wellness exams, immunizations, and routine screenings are covered at 100% with no member cost share when received from in-network providers.

Prescription Drugs

Tier | Retail (30 day) | Mail order (90 day)

Generic | $10 | $20

Preferred brand | $40 | $80

Non-preferred brand | $70 | $140

Specialty | $150 | N/A
```

### C.5 `out/rates.md`

Produced from `test_data/rates.xlsx` by the `xlsx` kind (no backend). Two worksheets are present in the workbook; each receives its own `## Sheet:` heading.

```
## Sheet: Premiums

tier | monthly_premium | employer_contribution
Employee only | 425 | 300
Employee + spouse | 812 | 550
Employee + family | 1180 | 800

## Sheet: Networks

network | states | hospitals_count
Acme National | all | 4200
Acme Regional | CA,OR,WA | 380
```

### C.6 `out/sbc_excerpt.md`

Produced from `test_data/sbc_excerpt.pdf` by the `ocr` kind with the `docling` backend. Exact byte equality is NOT guaranteed across Docling model weight versions or library updates; the structural contract is: a level-2 heading, a paragraph, a GFM pipe table with header / separator / data rows, and a trailing paragraph. A mistral-backend run of the same fixture would emit `[[START OF PAGE 1]]` / `[[END OF PAGE 1]]` markers around the body and would use an HTML table instead of a pipe table.

```
## Acme PPO Gold - Benefits at a Glance

The following table summarizes member cost-sharing for common covered services. All figures assume in-network providers unless noted.

| Service            | Deductible Applies   | Member Cost          |
|--------------------|----------------------|----------------------|
| Preventive care    | No                   | $0                   |
| Primary care visit | No                   | $25 copay            |
| Specialist visit   | No                   | $50 copay            |
| Inpatient hospital | Yes                  | 20% after deductible |
| Emergency room     | Yes                  | $300 copay then 20%  |

Out-of-network services are subject to a separate deductible and balance billing may apply. See the full SBC for details.
```

---

```
Files of record:
  app/services/mistral.py
  app/services/docling_ocr.py
  app/services/postprocess.py
  app/services/textextract.py
  out/intro.md
  out/policy.md
  out/benefits.md
  out/plan_summary.md
  out/rates.md
  out/sbc_excerpt.md
  graphify-out/GRAPH_REPORT.md
  graphify-out/cost.json
  graphify-out/graph.json
  graphify-out/graph.html
  graphify-out/manifest.json
  graphify-out/obsidian/
  graphify-out/wiki/
  graphify-out/cache/
```
