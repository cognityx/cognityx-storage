# Development

Install development dependencies and run the tests with:

```bash
uv sync --extra dev
uv run pytest
uv run mkdocs build --strict
```

Keep the public API provider-neutral. New backends should implement the
existing storage operations without exposing backend-specific paths to callers.

Run a strict documentation build and package build before publishing:

```bash
uv run mkdocs build --strict
uv build
```

Provider implementations should register a backend builder and an honest
available capability signature. Do not use the expected object or HDFS
templates as evidence that an implementation exists.
