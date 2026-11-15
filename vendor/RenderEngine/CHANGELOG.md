# Changelog

## 0.1.0 - Unreleased

- Bundled the framework-free browser renderer inside the Python package.
- Extracted the Python API and CLI as `replica-document-engine`.
- Established `from replica import convert_document` as the general Python API.
- Added PDF and EPUB adapters behind an extensible format registry.
- Established the format-neutral `replica-scene-schema` version 1 contract.
- Added atomic scene output, an independent `DocumentRegistry`, and a generic
  `ReplicaEngine` browser API with a compatibility alias for `PdfReplicaEngine`.
- Kept Highlighter focus analysis and UI outside the reusable packages.
