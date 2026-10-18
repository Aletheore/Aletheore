from aletheore.scope_filter import in_scope, is_test_path, normalize_under

def test_normalize_under_and_in_scope():
    assert normalize_under(["./pkg/", "a/b", ".", ""]) == ["pkg/", "a/b/"]
    assert in_scope("pkg/a.py", [], ["pkg/"], False) and not in_scope("other/a.py", [], ["pkg/"], False)
    assert not in_scope("pkg/locale/a.py", ["locale"], [], False) and in_scope("pkg/middleware/locale.py", ["locale"], [], False)
    assert not in_scope("pkg/test_a.py", [], [], True) and in_scope("pkg/a.py", [], [], True)


def test_is_test_path_conventions():
    for p in ("tests/a.py", "pkg/test_a.py", "pkg/a_test.go", "src/a.test.ts", "src/a.spec.js", "x/__tests__/a.js", "pkg/conftest.py"):
        assert is_test_path(p), p
    for p in ("pkg/a.py", "pkg/testing.py", "pkg/contest.go", "src/latest.ts"):
        assert not is_test_path(p), p
