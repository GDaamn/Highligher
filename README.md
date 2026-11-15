# BookLamp

BookLamp is a focused document reading application. It reconstructs uploaded PDF and EPUB files as
selectable page layouts, identifies focus words, and presents saved books in a
local library.

The document converters and browser renderer are included under `vendor/RenderEngine`.

## Deploy to Render

1. Push this folder to a Git repository with `render.yaml` and `Dockerfile` at its root.
2. In Render, choose **New → Blueprint**, connect the repository, review the paid Starter service and 1 GB persistent disk, and deploy.
3. Open the service URL. Check `/api/health` for the focus provider and supported file types, then upload a PDF or EPUB.

FastAPI serves the browser interface, renderer assets, and API from the same origin. The image installs Poppler for PDF source-page previews and runs `uv sync --locked --no-dev`. Uvicorn runs as one process on `0.0.0.0` using Render’s assigned `PORT`. Uploads, processed books, and reading preferences share the persistent disk. The hosted library starts empty. Anyone with access to the service can view and modify the shared library.

### Environment settings

| Variable | Purpose |
| --- | --- |
| `PORT` | Render supplies the listening port. The container defaults to 10000 locally. |
| `HIGHLIGHTER_DATA_DIR` | Blueprint uses `/var/data/booklamp` on the persistent disk. Local launches default to `data/`. |
| `FOCUS_PROVIDER` | Blueprint uses `heuristic` for offline focus analysis. To enable AI analysis, change the Blueprint value to `deepseek` and set the key below. |
| `DEEPSEEK_API_KEY` | Set as a secret in the Render service environment when using DeepSeek. |
| `DEEPSEEK_MODEL`, `DEEPSEEK_BASE_URL` | Optional overrides. Defaults are `deepseek-v4-flash` and `https://api.deepseek.com`. |
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

To use DeepSeek focus analysis, copy `.env.example` to `.env`, add your API key,
and set `FOCUS_PROVIDER=deepseek` before launching.

## Run from Terminal

Requirements: Python 3.12+ and `uv`.

```bash
uv sync --extra dev
export DEEPSEEK_API_KEY="your-key"
uv run uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8010
```

For offline use:

```bash
FOCUS_PROVIDER=heuristic uv run uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8010
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

The offline heuristic ranks complete sentences within short passages using recurring terms, claim cues, and questions. Unselected prose stays readable. Open **OpenRouter** on the home screen or reader toolbar, enter your key, and choose **Use Jev**. The tab sends passages through BookLamp to OpenRouter as you read, including in existing books. Reloading the tab or choosing **Clear key** removes the key. If a request fails, the reader shows the error and uses saved highlights.

For server-wide scoring during import, set `FOCUS_PROVIDER=jev` and `OPENROUTER_API_KEY` in the launcher’s .env or Render environment. `JEV_MODEL` defaults to `typesafe/jev-1.14`. Both modes use the OpenRouter Decisions API and a sentence relevance threshold of 0.65.
