"""Request-carried state: a value stored on the request in one handler and read in another."""
from pathlib import Path

from aletheore.flow.discover import discover_repo


def _scan(tmp_path: Path, name: str, src: str):
    (tmp_path / name).write_text(src)
    return discover_repo(tmp_path)


def test_express_middleware_state_reaches_handler(tmp_path):
    f = _scan(tmp_path, "app.js", """
const express = require("express");
const app = express();
app.use((req, res, next) => { req.stored = req.query.raw; next(); });
app.get("/label", (req, res) => { res.type("html").send(req.stored); });
""")
    assert [x["rule_id"] for x in f] == ["flow.javascript.xss"]


def test_express_middleware_state_after_handler_in_file(tmp_path):
    f = _scan(tmp_path, "app.js", """
const express = require("express");
const app = express();
app.get("/label", (req, res) => { res.type("html").send(req.stored); });
app.use((req, res, next) => { req.stored = req.query.raw; next(); });
""")
    assert len(f) == 1


def test_express_middleware_constant_state_is_quiet(tmp_path):
    f = _scan(tmp_path, "app.js", """
const express = require("express");
const app = express();
app.use((req, res, next) => { req.stored = "fixed"; next(); });
app.get("/label", (req, res) => { res.type("html").send(req.stored); });
""")
    assert f == []


def test_express_chained_send_alone_is_xss(tmp_path):
    f = _scan(tmp_path, "app.js", """
const express = require("express");
const app = express();
app.get("/x", (req, res) => { res.type("html").send(req.query.x); });
""")
    assert len(f) == 1


def test_go_context_value_reaches_sql(tmp_path):
    f = _scan(tmp_path, "main.go", """package main

import (
	"context"
	"database/sql"
	"fmt"
	"net/http"
)

var db *sql.DB

type ctxKey int

const prefKey ctxKey = 1

func withPref(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx := context.WithValue(r.Context(), prefKey, r.FormValue("pref"))
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func prefsHandler(w http.ResponseWriter, r *http.Request) {
	pref := r.Context().Value(prefKey).(string)
	q := fmt.Sprintf("SELECT l FROM prefs WHERE p = '%s'", pref)
	db.Query(q)
}
""")
    assert [x["rule_id"] for x in f] == ["flow.go.sql-injection"]


def test_go_context_value_constant_is_quiet(tmp_path):
    f = _scan(tmp_path, "main.go", """package main

import (
	"context"
	"database/sql"
	"fmt"
	"net/http"
)

var db *sql.DB

type ctxKey int

const prefKey ctxKey = 1

func withPref(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx := context.WithValue(r.Context(), prefKey, "dark")
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func prefsHandler(w http.ResponseWriter, r *http.Request) {
	pref := r.Context().Value(prefKey).(string)
	q := fmt.Sprintf("SELECT l FROM prefs WHERE p = '%s'", pref)
	db.Query(q)
}
""")
    assert f == []
