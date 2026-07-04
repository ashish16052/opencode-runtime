# Contributing to opencode-harness

## Dev setup

```bash
git clone https://github.com/ashishmohite/opencode-harness
cd opencode-harness

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
mypy src/opencode_harness
```

## Build the package

```bash
python -m build
python -m twine check dist/*
```

## Project layout

```
src/opencode_harness/   library source
tests/                  unit tests
```
