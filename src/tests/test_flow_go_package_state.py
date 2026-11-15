"""A Go package variable (or map) written from a request in one handler and read by another carries the request value."""
import pytest

from aletheore.flow.discover import discover_repo

HEAD = 'package main\nimport ("net/http";"os/exec")\n'
VAR = HEAD + 'var last string\nfunc set(w http.ResponseWriter, r *http.Request){{ last = {v} }}\nfunc run(w http.ResponseWriter, r *http.Request){{ exec.Command("sh","-c",last).Run() }}\nfunc main(){{ http.HandleFunc("/s", set); http.HandleFunc("/r", run); http.ListenAndServe(":80", nil) }}\n'
MAP = HEAD + 'var cfg = map[string]string{{}}\nfunc set(w http.ResponseWriter, r *http.Request){{ cfg["cmd"] = "echo " + {v} }}\nfunc run(w http.ResponseWriter, r *http.Request){{ exec.Command("sh","-c",cfg["cmd"]).Run() }}\nfunc main(){{ http.HandleFunc("/s", set); http.HandleFunc("/r", run); http.ListenAndServe(":80", nil) }}\n'


@pytest.mark.parametrize("tpl", [VAR, MAP])
def test_request_value_reaches_the_sink(tmp_path, tpl):
    (tmp_path / "main.go").write_text(tpl.format(v='r.URL.Query().Get("q")'))
    assert len(discover_repo(tmp_path)) == 1


@pytest.mark.parametrize("tpl", [VAR, MAP])
def test_constant_value_stays_quiet(tmp_path, tpl):
    (tmp_path / "main.go").write_text(tpl.format(v='"x"'))
    assert discover_repo(tmp_path) == []


MAPKEYS = HEAD + 'var saved = map[string]string{{"safe": "date"}}\nfunc Write(w http.ResponseWriter, r *http.Request){{ saved["attacker"] = r.FormValue("cmd") }}\nfunc Run(w http.ResponseWriter, r *http.Request){{ exec.Command("sh","-c",saved["{k}"]).Run() }}\n'


def test_other_literal_key_stays_quiet(tmp_path):
    (tmp_path / "main.go").write_text(MAPKEYS.format(k="safe"))
    assert discover_repo(tmp_path) == []


def test_same_literal_key_reports(tmp_path):
    (tmp_path / "main.go").write_text(MAPKEYS.format(k="attacker"))
    assert len(discover_repo(tmp_path)) == 1
