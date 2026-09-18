# Tests by area

Run the smallest relevant check while iterating, then run the complete suite
before opening a pull request.

| Area | Focused check |
| --- | --- |
| ranking and query language | `uv run pytest tests/test_ranking.py tests/test_querylang.py` |
| data import and local corpus | `uv run pytest tests/test_data_import.py tests/test_local_corpus.py` |
| translations | `uv run pytest tests/test_translations.py` |
| repository automation | `uv run pytest tests/test_repository_automation.py` |

The full verification command is `make test`. Include the focused and full
commands in the PR description.
