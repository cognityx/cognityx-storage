# Development

Install development dependencies and run the tests with:

```bash
uv sync --extra dev
uv run pytest
uv run mkdocs build --strict
```

Keep the public API provider-neutral. New backends should implement the
existing storage operations without exposing backend-specific paths to callers.
