# AGENTS.md

Guidance for AI agents (and humans) working in this repo.

## Project overview

This repo bridges Even Realities G2 smart glasses to a Hermes Agent gateway. Two paths exist:

- **Path A (recommended): `plugin/` + `glasses-app/`** — Full WebSocket protocol with streaming text, voice ASR, tool-call events, and session management. The plugin runs inside the Hermes Gateway; the glasses-app runs on the phone.
- **Path B (legacy): `bridge-server/`** — BYOA lite path using the glasses' built-in Add Agent mode. Simpler but no streaming/tools/sessions. Will be deleted once Path A is stable.

`probe/` contains throwaway integration test servers from BYOA development.

## Python tooling

All Python in this repo MUST use **[uv](https://docs.astral.sh/uv/)** for environment and dependency management, declared via a **`pyproject.toml`** in the package directory. No `requirements.txt`, no `pip install`, no manual venv activation.

A Python package in this repo (e.g. `plugin/`, `bridge-server/`, `probe/`) MUST have:

- A `pyproject.toml` declaring dependencies under `[project.dependencies]`
- A `[build-system]` section using **`uv_build`** as the build backend (not hatchling, setuptools, or other backends):

  ```toml
  [build-system]
  requires = ["uv_build>=0.11.25,<0.12"]
  build-backend = "uv_build"
  ```

- Source code in the standard `src/<package_name>/` layout (e.g. `src/byoa_plugin/`), importable as `<package_name>` after install
- A `uv.lock` committed alongside it
- Dependencies installed via `uv sync` (creates `.venv` automatically and installs the project as a package)
- Commands run via `uv run <command>` — never activate a venv manually

Conventions:

- To add a dependency: `uv add <package>` (updates `pyproject.toml` and `uv.lock` together)
- To run a Python entry point: `uv run python -m <package_name>` or `uv run <entrypoint>`
- To run a uvicorn app: `uv run uvicorn <package_name>.<module>:<app> ...`
- To build a wheel/sdist: `uv build`

This applies to all current and future Python in this repo. Existing `requirements.txt` files are legacy and SHOULD be migrated to `pyproject.toml` (with `uv_build` build backend and `src/` layout) when their owning package is next touched.

### Python linting and tests

- **Linter**: `ruff` with `ALL` rules enabled and Google docstring convention
- **Type checker**: `basedpyright`
- **Test runner**: `pytest` with `pytest-asyncio` (auto mode)

```bash
cd plugin/
uv run ruff check src/ tests/        # lint
uv run pytest tests/                 # run tests
uv run pytest tests/ -q              # quiet mode
```

Ruff config lives in `plugin/pyproject.toml` under `[tool.ruff]`. Docstrings follow **Google style** (`[tool.ruff.lint.pydocstyle] convention = "google"`).

Test-specific lint relaxations are in `[tool.ruff.lint.per-file-ignores]` under `"tests/**/*.py"` — each ignore has an inline comment justifying it.

## TypeScript tooling (glasses-app/)

The glasses-app uses **Vite + TypeScript** with the Even Hub SDK.

```bash
cd glasses-app/
npm install                          # install deps
npm run build                        # typecheck (tsc) + bundle (vite)
npm run test                         # run Vitest suite
npm run test:watch                   # Vitest in watch mode
npm run lint                         # ESLint (flat config)
npm run typecheck                    # tsc --noEmit only
```

### TypeScript conventions

- **Strict mode**: `tsconfig.json` has `"strict": true`
- **Linter**: ESLint 9 flat config (`eslint.config.js`) with `typescript-eslint` type-checked rules
- **Test runner**: Vitest with node environment
- **SDK**: `@evenrealities/even_hub_sdk` — always use the latest published version

Container objects (`TextContainerProperty`, `TextContainerUpgrade`, `CreateStartUpPageContainer`) are **classes** — instantiate with `new`, don't pass plain objects.

Event types use the `OsEventTypeList` enum (not magic numbers). Import from the SDK:

```typescript
import { OsEventTypeList } from '@evenrealities/even_hub_sdk';
```

### Pure logic extraction

Stateful integration code (`main.ts`) should stay thin. Pure logic belongs in `src/lib/` modules with corresponding `tests/` files:

- `src/lib/frames.ts` — frame reducer (pure state + effects)
- `src/lib/session.ts` — session name truncation
- `src/lib/reconnect.ts` — exponential backoff calculation
- `src/lib/state.ts` — snapshot serialize/parse/merge

## Protocol codegen

The WS wire format has **one source of truth**: `plugin/src/byoa_plugin/protocol.py`. The TypeScript definitions are generated:

```bash
cd plugin/
uv run python -m byoa_plugin.protocol_gen > ../glasses-app/src/protocol.ts
```

Always regenerate `glasses-app/src/protocol.ts` after changing frame schemas in `protocol.py`. Commit the regenerated file alongside the Python change.

## Security

**Never commit private hostnames, domains, tailnet names, API keys, or hardware details.** Use generic placeholders:

| Use | Not |
|---|---|
| `hermes.local`, `litellm.local` | your actual domain |
| `hermes.your-tailnet.ts.net` | your actual Tailscale FQDN |
| `whisper` (generic model name) | your specific deployed model name |
| `sk-litellm-change-me` | real API keys |
| `your gateway host` | specific hardware (e.g. "Framework Desktop with AMD Ryzen") |

`.env` and `.env.*` are gitignored (except `.env.example`). Real secrets must never be committed.

## Git conventions

- **Conventional commits**: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:` with optional scope (`feat(plugin): ...`)
- **Atomic commits**: each commit should be independently revertable. Split by module/directory. Pair tests with implementation.
- **No cache files**: `__pycache__/`, `.ruff_cache/`, `.pytest_cache/`, `*.pyc`, `node_modules/`, `dist/`, `.omo/`, `.opencode/` are all gitignored. Never `git add` them.
