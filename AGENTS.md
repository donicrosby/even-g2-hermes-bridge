# AGENTS.md

Guidance for AI agents (and humans) working in this repo.

## Python tooling

All Python in this repo MUST use **[uv](https://docs.astral.sh/uv/)** for environment and dependency management, declared via a **`pyproject.toml`** in the package directory. No `requirements.txt`, no `pip install`, no manual venv activation.

A Python package in this repo (e.g. `bridge-server/`, `probe/`) MUST have:

- A `pyproject.toml` declaring dependencies under `[project.dependencies]`
- A `[build-system]` section using **`uv_build`** as the build backend (not hatchling, setuptools, or other backends):

  ```toml
  [build-system]
  requires = ["uv_build>=0.11.25,<0.12"]
  build-backend = "uv_build"
  ```

- Source code in the standard `src/<package_name>/` layout (e.g. `src/byoa_probe/`), importable as `<package_name>` after install
- A `uv.lock` committed alongside it
- Dependencies installed via `uv sync` (creates `.venv` automatically and installs the project as a package)
- Commands run via `uv run <command>` — never activate a venv manually

Conventions:

- To add a dependency: `uv add <package>` (updates `pyproject.toml` and `uv.lock` together)
- To run a Python entry point: `uv run python -m <package_name>` or `uv run <entrypoint>`
- To run a uvicorn app: `uv run uvicorn <package_name>.<module>:<app> ...`
- To build a wheel/sdist: `uv build`

This applies to all current and future Python in this repo. Existing `requirements.txt` files are legacy and SHOULD be migrated to `pyproject.toml` (with `uv_build` build backend and `src/` layout) when their owning package is next touched.
