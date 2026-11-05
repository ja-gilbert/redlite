# Mutation testing

## Tool

A custom runner script, not a mutation library. Each mutant is one exact text
replacement in one file under `redlite/`. Each parallel worker holds its own copy
of the tree. For every mutant the runner writes the replacement into a worker's
copy, runs the whole test suite from inside that copy, records which tests fail,
then restores the file. A mutant counts as killed if any test fails. Settings: an
8-second per-test timeout through pytest-timeout, a 900-second cap per suite run,
14 parallel workers.

The 284 mutants are realistic slips across all five modules: 161 written by hand
and 123 proposed by AI reviewers, after duplicates were removed. The runner and
the mutant list are not in this repository.

## Command

Run once per mutant, from inside the mutated copy, so the copy shadows the
installed package:

    uv run --project <project> --no-sync --with pytest-timeout \
        python -m pytest -q -rA --tb=no -p no:cacheprovider --timeout=8 tests

## Results

| Date       | Code                              | Suite                | Mutants | Killed | Survived | Kill rate |
|------------|-----------------------------------|----------------------|--------:|-------:|---------:|----------:|
| 2026-09-15 | 42c13d4, before the simplify pass | 76 tests             |     284 |    223 |       61 |     78.5% |
| 2026-11-05 | 42c13d4                           | 55 tests from PR #21 |     284 |    223 |       61 |     78.5% |
| 2026-11-05 | 45972e9, current main             | 55 tests             |     266 |    219 |       47 |     82.3% |

On this mutant set, the 55-test suite kills exactly the same 223 mutants as the
76-test suite it replaced. On current main, 18 of the 284 mutants no longer apply,
because the simplify pass rewrote the code they target. Both 2026-11-05 runs on
main produced identical survivor sets.

Test counts are pytest's collected items. The 55 are 50 test functions, some of
them parametrized.

Surviving mutants on main, by file: `__main__.py` 15 of 38, `client.py` 11 of 36,
`server.py` 16 of 96, `store.py` 3 of 54, `protocol.py` 2 of 42. Survivors have
not been classified as equivalent or not; they are treated as known gaps.
