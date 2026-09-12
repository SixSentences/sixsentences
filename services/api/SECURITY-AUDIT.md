# Community service audit gates

Before a public commit or image is released, run the following from the
repository root:

```console
uv run --project services/api --frozen python services/api/scripts/audit_boundary.py
uv run --project services/api --frozen python services/api/scripts/check_web_contracts.py --require-complete
uv run --project services/api --frozen pytest services/api/tests
uv run --project services/api --frozen ruff check services/api/src services/api/scripts services/api/tests
uv run --project services/api --frozen mypy services/api/src
```

The boundary audit rejects credential signatures, assigned secret literals,
production URLs and addresses, private paths, runtime commercial/hosted modules,
data stores, logs, archives, caches, symlinks and unpinned direct dependencies.
Tests additionally exercise migrations, authentication, organization isolation,
queue/SMTP behavior and consent-bound public text and spoken interviews.

These working-tree checks supplement, but do not replace, a full public-history
secret scan, dependency/license and container scans, independent code review,
deployment threat modelling, processor assessment and restore testing.
