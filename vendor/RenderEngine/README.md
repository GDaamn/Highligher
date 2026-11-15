# RenderEngine

This repository contains the reusable document reconstruction toolkit used by
BookLamp and other products. Format adapters normalize PDF and EPUB inputs into a
versioned scene; the framework-free browser renderer displays that scene as
selectable, optionally editable text. Product UI and AI focus-word selection
live separately in the sibling `BookLamp` project.

## Components

| Component | Published with | Responsibility |
| --- | --- | --- |
| `src/replica/` | `replica-document-engine` on PyPI | Format adapters, conversion API, CLI, and bundled browser renderer |
| `examples/web/` | Not published separately | Example UI and renderer tests |
| `shared/schema/` | Repository contract | Versioned manifest and page JSON Schemas |

Package names must be checked for registry availability before the first public
release. The distribution is installed as `replica-document-engine` and imported
as `replica`. There is no separate frontend package.

## Install this workspace

Requirements: Python 3.12+ and `uv`. Node.js and pnpm are needed only to run
the optional web-example tests.

```bash
uv sync
```

Poppler's `pdftoppm` command is needed only for rendered source-page previews.
Set `REPLICA_PDFTOPPM=/absolute/path/to/pdftoppm` when it is not on `PATH`.

## Use the converter

```python
from replica import convert_document

manifest = convert_document(
    "input.epub",  # PDF and EPUB are detected automatically
    "output/scene",
)
```

`convert_pdf(...)` and `convert_epub(...)` remain available when an integration
wants to select a format explicitly.

Or from the command line:

```bash
uv run replica-convert input.epub --output output/scene
```

## Add another input format

Implement a `DocumentAdapter` with signature detection, metadata inspection,
and a converter that writes the shared Replica scene contract. Register it once:

```python
from replica import DocumentAdapter, register_adapter

register_adapter(DocumentAdapter(
    name="docx",
    extensions=frozenset({".docx"}),
    media_types=frozenset({"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
    matches=is_docx,
    inspect=inspect_docx,
    convert=convert_docx,
))
```

BookLamp and other consumers can continue calling `convert_document(...)`.
For isolated plugin or test environments, create a `DocumentRegistry`, register
adapters on it, and call its `convert_document()` method without changing the
process-wide default registry.

## Serve the bundled renderer

The Python package exposes the renderer directory for frameworks that serve
static files:

```python
from fastapi.staticfiles import StaticFiles
from replica import web_assets_path

app.mount("/replica", StaticFiles(directory=web_assets_path()))
```

Your UI can then load it directly:

```js
import { ReplicaEngine } from "/replica/replica-engine.js";

const viewer = new ReplicaEngine({
  root: document.querySelector("#viewer"),
  baseUrl: "/documents/example/scene",
  continuous: true,
  editable: false,
});

await viewer.load();
viewer.setSpread(true);
```

## Repository structure

```text
src/replica/               Format registry, converters, and public API
  web/                     Renderer JavaScript and CSS bundled in the wheel
examples/web/              Private example UI, sample scene, and renderer tests
scripts/                   Development and compatibility utilities
tests/                     Python package contract tests
shared/schema/             Scene-format JSON schemas
docs/                      Architecture and release documentation
```

## Verify

```bash
uv sync --extra dev && uv run pytest -q && uv build
cd examples/web && pnpm install && pnpm test
```

See [docs/architecture.md](docs/architecture.md) for boundaries and
[docs/publishing.md](docs/publishing.md) for the release checklist.

## Current limits

- Image-only scans need an OCR adapter before they contain editable words.
- EPUB is reflowable, so its adapter creates stable Replica pages rather than
  claiming to reproduce a nonexistent fixed source pagination.
- Embedded PDF fonts are extracted when possible; browser font support still
  determines whether a particular font file can be used.
- BookLamp uses the renderer in read-only mode, while the editing API remains
  available to other products.
