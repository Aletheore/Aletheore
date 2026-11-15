from aletheore.endpoints import map_api_endpoints


def _routes(tmp_path, code):
    (tmp_path / "main.go").write_text(code)
    return sorted((e["framework"], e["method"], e["path"]) for e in map_api_endpoints(tmp_path)["endpoints"])


def test_chi_verbs_and_nested_route_closures_compose_prefixes(tmp_path):
    code = """package main
import "github.com/go-chi/chi/v5"
func main() {
	r := chi.NewRouter()
	r.Get("/users", list)
	r.Route("/api", func(r chi.Router) {
		r.Post("/items", add)
		r.Route("/v2", func(r chi.Router) { r.Delete("/items/{id}", del) })
	})
	r.Method("PUT", "/thing", put)
	r.HandleFunc("/any", anyh)
}
"""
    assert _routes(tmp_path, code) == [
        ("chi", "ANY", "/any"),
        ("chi", "DELETE", "/api/v2/items/{id}"),
        ("chi", "GET", "/users"),
        ("chi", "POST", "/api/items"),
        ("chi", "PUT", "/thing"),
    ]


def test_chi_group_closure_without_a_path_adds_no_prefix(tmp_path):
    code = """package main
import "github.com/go-chi/chi/v5"
func main() {
	r := chi.NewRouter()
	r.Group(func(r chi.Router) { r.Get("/private", p) })
}
"""
    assert _routes(tmp_path, code) == [("chi", "GET", "/private")]


def test_echo_routes_are_labelled_echo_and_follow_groups(tmp_path):
    code = """package main
import "github.com/labstack/echo/v4"
func main() {
	e := echo.New()
	e.GET("/users", list)
	e.HEAD("/ping", ping)
	g := e.Group("/api")
	g.POST("/items", add)
	e.Any("/any", anyh)
}
"""
    assert _routes(tmp_path, code) == [
        ("echo", "ANY", "/any"),
        ("echo", "GET", "/users"),
        ("echo", "HEAD", "/ping"),
        ("echo", "POST", "/api/items"),
    ]


def test_fiber_routes_follow_groups(tmp_path):
    code = """package main
import "github.com/gofiber/fiber/v2"
func main() {
	app := fiber.New()
	app.Get("/users", list)
	api := app.Group("/api")
	api.Post("/items", add)
	app.All("/any", anyh)
}
"""
    assert _routes(tmp_path, code) == [
        ("fiber", "ANY", "/any"),
        ("fiber", "GET", "/users"),
        ("fiber", "POST", "/api/items"),
    ]


def test_gin_file_is_unchanged_by_the_framework_split(tmp_path):
    code = 'package main\nimport "github.com/gin-gonic/gin"\nfunc main() {\n\tr := gin.Default()\n\tr.GET("/x", h)\n}\n'
    assert _routes(tmp_path, code) == [("gin", "GET", "/x")]
