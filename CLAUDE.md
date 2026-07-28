## Repo Layout

A monorepo of independently-deployable components, grouped by role rather
than by product — `agents/` and `mcp-servers/` are siblings so an MCP
server can be shared by more than one agent, each of which is deployed to
its own GCP project (a Google Chat API constraint — see deploy.sh):

```
agents/seo-workbook-agent/       ADK conversational agent
mcp-servers/seo-workbook-mcp/    FastMCP tool server (also has data/, this
                                 component's best-practices CSV + legacy
                                 workbook imports)
shared/                          seo-workbook-common — cross-cutting code
                                 shared by both of the above
```

Each component uses a `src/` layout (`<component>/src/<package_name>/`,
tests at `<component>/tests/`).

## Test Before Push

There's no build/compile step, so this replaces the npm build+test gate.
Before committing and pushing, run each package's suite from its own
directory and confirm everything passes:

```bash
cd shared && .venv/bin/pytest
cd mcp-servers/seo-workbook-mcp && .venv/bin/pytest
cd agents/seo-workbook-agent && .venv/bin/pytest
```

First time in a fresh clone, set up each package's venv before running its
tests (see that package's `pyproject.toml` for its exact dependencies):

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

`shared` also has `output`/`storage`/`legacy_import` extras (Jinja2/Google
Sheets API, PyMongo, gspread — only needed by the mcp-server at runtime,
but by shared's own tests too, since they cover that code): install it
there with `.[dev,output,storage,legacy_import]` instead.

The mcp-server and agent both depend on `shared`; rather than an editable
cross-package install (flaky in this environment with hatchling — see the
note in their `pyproject.toml` files), they resolve it via pytest's
`pythonpath` setting for tests, and expect `PYTHONPATH` to include
`../../shared/src` when actually running the service outside of pytest.


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
