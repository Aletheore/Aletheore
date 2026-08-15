# Raw model outputs (verbatim, unedited)

All three tests below used the identical raw data files in this directory as input, via a single `simple_completion(system_prompt, user_prompt)` call — no tools, no code execution, no multi-turn refinement. Adapter: `aletheore.adapters.openai_compatible.OpenAICompatibleAdapter`, `extra_body=None` (provider default reasoning level). See `../scripts/` for the exact scripts that produced these.

---

## Test 1: Hotspots (`hotspots_input_1500commits.txt` -> top 10 most-touched files)

### gpt-5.6-terra

| Rank | File path | Commits touching it |
|---:|---|---:|
| 1 | `CHANGES.rst` | 218 |
| 2 | `src/flask/app.py` | 154 |
| 3 | `src/flask/helpers.py` | 123 |
| 4 | `requirements/dev.txt` | 117 |
| 5 | `.pre-commit-config.yaml` | 105 |
| 6 | `src/flask/blueprints.py` | 96 |
| 7 | `src/flask/cli.py` | 94 |
| 8 | `src/flask/scaffold.py` | 92 |
| 9 | `tests/test_basic.py` | 89 |
| 10 | `pyproject.toml` | 83 |

### gpt-5.6-luna

> I'm unable to reliably produce exact counts from this extremely large log without programmatically parsing it. Use the following script to obtain the exact top 10:
>
> ```python
> from collections import Counter
> import sys
>
> counts = Counter()
> current_files = []
>
> for line in sys.stdin:
>     line = line.rstrip("\n")
>
>     if line.startswith("COMMIT "):
>         # Count files from the preceding commit once.
>         counts.update(current_files)
>         current_files = []
>     elif line:
>         current_files.append(line)
>
> # Count the final commit's files.
> counts.update(current_files)
>
> for path, count in counts.most_common(10):
>     print(f"{count:>5} {path}")
> ```
>
> Save the supplied log as `git-log.txt`, then run:
>
> ```bash
> python count_files.py < git-log.txt
> ```

---

## Test 2: Ownership (`ownership_input_1500commits.txt` -> top 8 authors by commit count)

### gpt-5.6-terra

Counted by email address (so `pgjones` and `Phil Jones` are combined under `philip.graham.jones@googlemail.com`).

| Rank | Author / email | Commits | Exact percentage of 1,500 |
|---:|---|---:|---:|
| 1 | David Lord — davidism@gmail.com | 1,065 | 71% |
| 2 | Grey Li — withlihui@gmail.com | 65 | 13/3% (4⅓%) |
| 3 | dependabot[bot] | 61 | 61/15% (4 1/15%) |
| 4 | pgjones / Phil Jones | 47 | 47/15% (3 2/15%) |
| 5 | pre-commit-ci[bot] | 39 | 13/5% (2.6%) |
| 6 | dependabot-preview[bot] | 31 | 31/15% (2 1/15%) |
| 7 | Frank Yu — withyuxiaoy@gmail.com | 6 | 2/5% (0.4%) |
| 8 (tie) | Adrian Moennich | 5 | 1/3% |
| 8 (tie) | Maxim G. Ivanov — ivanovmg@gmail.com | 5 | 1/3% |

### gpt-5.6-luna

| Rank | Author | Email | Commits | Percentage of 1,500 |
|---:|---|---|---:|---:|
| 1 | David Lord | davidism@gmail.com | 1,116 | 74.4% |
| 2 | Grey Li | withlihui@gmail.com | 77 | 77/15% |
| 3 | dependabot[bot] | ...@users.noreply.github.com | 48 | 3.2% |
| 4 | pgjones / Phil Jones | philip.graham.jones@googlemail.com | 44 | 44/15% |
| 5 | pre-commit-ci[bot] | ...@users.noreply.github.com | 38 | 38/15% |
| 6 | dependabot-preview[bot] | ...@users.noreply.github.com | 25 | 5/3% |
| 7 | Adrian Moennich | adrian@planetcoding.net | 5 | 1/3% |
| 8 | Maxim G. Ivanov | ivanovmg@gmail.com | 5 | 1/3% |

---

## Test 3: Dead code / unreachable modules (`deadcode_input_83files_imports.txt`)

### gpt-5.6-terra

```
docs/conf.py
examples/celery/make_celery.py
examples/celery/src/task_app/views.py
examples/javascript/tests/conftest.py
examples/javascript/tests/test_js_example.py
examples/tutorial/flaskr/auth.py
examples/tutorial/flaskr/blog.py
examples/tutorial/tests/conftest.py
examples/tutorial/tests/test_auth.py
examples/tutorial/tests/test_blog.py
examples/tutorial/tests/test_db.py
examples/tutorial/tests/test_factory.py
src/flask/__main__.py
src/flask/debughelpers.py
tests/conftest.py
tests/test_appctx.py
tests/test_async.py
tests/test_basic.py
tests/test_blueprints.py
tests/test_cli.py
tests/test_config.py
tests/test_converters.py
tests/test_helpers.py
tests/test_instance_config.py
tests/test_json.py
tests/test_json_tag.py
tests/test_logging.py
tests/test_regression.py
tests/test_reqctx.py
tests/test_request.py
tests/test_session_interface.py
tests/test_signals.py
tests/test_subclassing.py
tests/test_templating.py
tests/test_testing.py
tests/test_user_error_handler.py
tests/test_views.py
tests/test_apps/blueprintapp/__init__.py
tests/test_apps/cliapp/__init__.py
tests/test_apps/cliapp/app.py
tests/test_apps/cliapp/factory.py
tests/test_apps/cliapp/importerrorapp.py
tests/test_apps/cliapp/inner1/__init__.py
tests/test_apps/cliapp/inner1/inner2/__init__.py
tests/test_apps/cliapp/inner1/inner2/flask.py
tests/test_apps/cliapp/multiapp.py
tests/test_apps/helloworld/wsgi.py
tests/test_apps/subdomaintestmodule/__init__.py
tests/type_check/typing_app_decorators.py
tests/type_check/typing_error_handler.py
tests/type_check/typing_route.py
```

> This is only based on the shown static import statements; it does not account for pytest discovery, CLI module-name strings, `python -m`, dynamic imports, or framework-driven loading.

(49 files flagged)

### gpt-5.6-luna

```
docs/conf.py

examples/celery/make_celery.py
examples/celery/src/task_app/views.py

examples/tutorial/flaskr/blog.py

examples/javascript/tests/conftest.py
examples/javascript/tests/test_js_example.py

examples/tutorial/tests/conftest.py
examples/tutorial/tests/test_auth.py
examples/tutorial/tests/test_blog.py
examples/tutorial/tests/test_db.py
examples/tutorial/tests/test_factory.py

src/flask/__main__.py
src/flask/debughelpers.py

tests/conftest.py
tests/test_appctx.py
tests/test_async.py
tests/test_basic.py
tests/test_blueprints.py
tests/test_cli.py
tests/test_config.py
tests/test_converters.py
tests/test_helpers.py
tests/test_instance_config.py
tests/test_json.py
tests/test_json_tag.py
tests/test_logging.py
tests/test_regression.py
tests/test_reqctx.py
tests/test_request.py
tests/test_session_interface.py
tests/test_signals.py
tests/test_subclassing.py
tests/test_templating.py
tests/test_testing.py
tests/test_user_error_handler.py
tests/test_views.py

tests/test_apps/cliapp/__init__.py
tests/test_apps/cliapp/app.py
tests/test_apps/cliapp/factory.py
tests/test_apps/cliapp/importerrorapp.py
tests/test_apps/cliapp/inner1/__init__.py
tests/test_apps/cliapp/inner1/inner2/__init__.py
tests/test_apps/cliapp/inner1/inner2/flask.py
tests/test_apps/cliapp/multiapp.py
tests/test_apps/helloworld/wsgi.py
tests/test_apps/subdomaintestmodule/__init__.py

tests/type_check/typing_app_decorators.py
tests/type_check/typing_error_handler.py
tests/type_check/typing_route.py
```

(No caveat appended. 48 files flagged - one fewer than Terra: Luna omitted `examples/tutorial/flaskr/auth.py` and `tests/test_apps/blueprintapp/__init__.py`.)
