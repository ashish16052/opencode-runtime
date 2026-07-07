# Contributing to opencode-runtime

## Dev setup

```bash
git clone https://github.com/ashish16052/opencode-runtime
cd opencode-runtime

python3 -m venv venv
source venv/bin/activate

pip install -e ".[dev]"
```

## Run tests

```bash
pytest
```

## Lint and format

```bash
ruff check .
ruff format .
```

## Type check

```bash
mypy src/opencode_runtime
```

## Build the package

```bash
python -m build
python -m twine check dist/*
```

## Project layout

```
src/opencode_runtime/   library source
tests/                  unit tests
```
