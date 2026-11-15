# BookLamp

BookLamp is a focused document reading application. It reconstructs uploaded PDF and EPUB files as
selectable page layouts, identifies focus words, and presents saved books in a
local library.

The document converters and browser renderer are included under `vendor/RenderEngine`.

## Deploy to Render

1. Push this folder to a Git repository with `render.yaml` and `Dockerfile` at its root.
2. In Render, choose **New → Blueprint**, connect the repository, review the paid Starter service and 1 GB persistent disk, and deploy.
3. Open the service URL. Check `/api/health` for the focus provider and supported file types, then upload a PDF or EPUB.

FastAPI serves the browser interface, renderer assets, and API from the same origin. The image installs Poppler for PDF source-page previews and runs `uv sync --locked --no-dev`. Uvicorn runs as one process on `0.0.0.0` using Render’s assigned `PORT`. Uploads, processed books, and reading preferences share the persistent disk. Each browser profile has a private library identified by an HTTP-only cookie. Clearing the cookie loses access. People using the same browser profile share its library. The server stores and processes the files. Existing uploads from the older shared library stay on disk and are excluded from visitor libraries. Re-upload a book to add it to a private library.

### Environment settings

| Variable | Purpose |
| --- | --- |
| `PORT` | Render supplies the listening port. The container defaults to 10000 locally. |
| `HIGHLIGHTER_DATA_DIR` | Blueprint uses `/var/data/booklamp` on the persistent disk. Local launches default to `data/`. |
| `OPENROUTER_API_KEY` | Set this secret in Render to enable OpenRouter scoring during import. Without it, uploads use local sentence highlighting. |
| `MAX_UPLOAD_MB` | Per-file upload limit in MiB. Defaults to 100. |
| `FOCUS_BATCH_CHARACTERS`, `FOCUS_CONCURRENCY` | Focus analysis batch size and concurrency. Defaults are 42000 and 24. |
| `REPLICA_ENGINE_DIR`, `REPLICA_PDFTOPPM` | Optional paths to custom browser renderer assets and the Poppler executable. The image includes both. |

## Launch on macOS

Double-click **Launch BookLamp.command** in Finder. BookLamp starts privately on
your Mac and opens [http://127.0.0.1:8010](http://127.0.0.1:8010) in your default
browser. Keep the Terminal window open while using the app; closing it stops the
local server.

The launcher uses offline focus analysis by default, so it works without an API
key. On a new Mac it needs Python 3.12+ and [`uv`](https://docs.astral.sh/uv/); it
will prepare the app's isolated environment automatically on first launch.

For server-side OpenRouter scoring on macOS, set `OPENROUTER_API_KEY` in the launcher’s `.env` file.

## Run from Terminal

Requirements: Python 3.12+ and `uv`.

```bash
uv sync --extra dev
uv run uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8010
```

Open `http://127.0.0.1:8010`.

## Structure

```text
backend/       FastAPI server, processing jobs, metadata, and focus analysis
frontend/      Highlighter interface and reader controls
data/          Local processed-book cache (ignored by Git)
```

## Verify

```bash
uv run pytest -q
```

### Sentence highlighting

The offline heuristic ranks complete sentences within short passages using recurring terms, claim cues, and questions. Unselected prose stays readable.

For server-wide scoring during import, set `OPENROUTER_API_KEY` in the launcher’s .env or Render environment. `JEV_MODEL` defaults to `typesafe/jev-1.14`. Server-side scoring uses the OpenRouter Decisions API and a sentence relevance threshold of 0.65.
