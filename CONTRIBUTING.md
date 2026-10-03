# Contributing

Thanks for helping! The short version:

```bash
make dev            # set up .venv
make test lint      # must pass
make fuzz           # for parser changes: 200,000 fuzzed inputs must pass
```

- Read [docs/development.md](docs/development.md) (layout, principles, adding a standard) and
  [docs/testing.md](docs/testing.md) (what's tested and how).
- Every bug fix needs a regression test; every new output field needs a JSON Schema update.
- Sample files must be **synthetic**. Never commit real EDI, PHI, or partner data.
- Keep the core library dependency-free.
- Commits: small and descriptive; PRs describe what changed and how it was tested.
