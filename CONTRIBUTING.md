# Contributing

## Install

To make changes to this project, first clone this repository:

```sh
git clone https://github.com/mozilla/wagtail-localize-smartling.git
cd wagtail-localize-smartling
```

With your preferred virtualenv activated, install testing dependencies:

### Using pip

```sh
python -m pip install --upgrade pip>=21.3
python -m pip install -e '.[test]' -U
```

### Using flit

```sh
python -m pip install flit
flit install
```

## pre-commit

This project uses [pre-commit](https://github.com/pre-commit/pre-commit) to
enforce formatting and code quality. It is included in the project testing
requirements. To set it up locally:

```sh
# go to the project directory
cd wagtail-localize-smartling
# initialize pre-commit
pre-commit install

# Optional, run all checks once. After this, checks will run only on changed files.
pre-commit run --all
```

## Running tests

We use [`tox`](https://tox.wiki/) to run tests. `tox` is included in the project's testing dependencies. To run all the tests:

```sh
tox
```

... or, to run tests for a specific environment, use the `-e` flag:

```sh
tox -e python3.11-django4.2-wagtail5.1-sqlite wagtail-localize-smartling.tests.test_file.TestClass.test_method
```

To run all environments for a particular combination of factors (e.g. Python 3.12 and SQLite), use the `-f` flag:

```sh
tox -f python3.12 sqlite
```


## Interative use

To run the test app interactively, use `./testmanage.py` instead of
`./manage.py` and then run commands as you would for a normal Django project
(e.g. `./testmanage.py runserver 0.0.0.0:8000`, `./testmanage.py
createcachetable`, etc.).
