## Test Before Push

This is a Python monorepo with three independently-tested packages — there's no
build/compile step, so this replaces the npm build+test gate. Before
committing and pushing, run each package's suite from its own directory and
confirm everything passes:

```bash
cd common && .venv/bin/pytest
cd mcp_server && .venv/bin/pytest
cd agent_service && .venv/bin/pytest
```

First time in a fresh clone, set up each package's venv before running its
tests (see that package's `pyproject.toml` for its exact dependencies):

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

`mcp_server` and `agent_service` both depend on `common`; rather than an
editable cross-package install (flaky in this environment with hatchling —
see the note in their `pyproject.toml` files), they resolve it via pytest's
`pythonpath` setting for tests, and expect `PYTHONPATH` to include `../common`
when actually running the service outside of pytest.


## Commit Message Format

Use Conventional Commits for all commits.

- `feat`: new features
- `fix`: bug fixes
- `test`: testing-related changes
- `docs`: documentation changes
- `chore`: maintenance tasks
- `refactor`: refactoring without behavior changes
- `perf`: performance improvements
- `ci`: CI/CD changes
- `style`: formatting or style-only changes

Examples:

```text
feat: add product inventory reconciliation endpoint
fix: prevent tenant data leak in customer query
test: add invoice service tax edge case coverage
```

## Commit Helper

You can use the helper script in this repository:

```bash
./scripts/commit-helper.sh <type> "commit message"
```

There's no enforced `commit-msg` hook or CI check on message format in this
repo yet — Conventional Commits here is convention only, followed manually
(or via the helper script above) rather than mechanically enforced.
