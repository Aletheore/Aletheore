from aletheore.endpoints import map_api_endpoints


def _routes(tmp_path, code):
    (tmp_path / "routes.rs").write_text(code)
    eps = map_api_endpoints(tmp_path)["endpoints"]
    return sorted((e["method"], e["path"]) for e in eps)


def test_nested_scopes_compose(tmp_path):
    code = """
use actix_web::web;
pub fn routes(cfg: &mut web::ServiceConfig) {
    cfg.service(
        web::scope("/api")
            .service(web::scope("/tags").route("", web::get().to(tags)))
            .service(
                web::scope("/articles")
                    .route("/feed", get().to(feed))
                    .service(web::scope("/{slug}").route("", delete().to(remove))),
            ),
    );
}
"""
    assert _routes(tmp_path, code) == [
        ("DELETE", "/api/articles/{slug}"),
        ("GET", "/api/articles/feed"),
        ("GET", "/api/tags"),
    ]


def test_resource_routes(tmp_path):
    code = """
use actix_web::web;
fn cfg(c: &mut web::ServiceConfig) {
    c.service(web::scope("/v1").service(
        web::resource("/users").route(web::get().to(list)).route(web::post().to(make))
    ));
}
"""
    assert _routes(tmp_path, code) == [("GET", "/v1/users"), ("POST", "/v1/users")]


def test_each_route_once(tmp_path):
    code = """
use actix_web::web;
fn cfg(c: &mut web::ServiceConfig) {
    c.service(web::scope("/a").service(web::scope("/b").route("/c", web::get().to(h))));
}
"""
    assert _routes(tmp_path, code) == [("GET", "/a/b/c")]


def test_axum_still_used_without_actix(tmp_path):
    code = 'fn r() { Router::new().route("/x", get(h)); }\n'
    assert _routes(tmp_path, code) == [("GET", "/x")]


def test_attribute_macro_routes(tmp_path):
    code = """
use actix_web::{get, post, HttpResponse};

#[get("/health")]
async fn health() -> HttpResponse { HttpResponse::Ok().finish() }

#[post("/items/{id}")]
pub async fn make(id: String) -> HttpResponse { HttpResponse::Ok().finish() }
"""
    assert _routes(tmp_path, code) == [("GET", "/health"), ("POST", "/items/{id}")]


def test_attribute_route_registered_under_a_scope_gets_the_prefix(tmp_path):
    code = """
use actix_web::{get, web, App, HttpServer};

#[get("/health")]
async fn health() -> &'static str { "ok" }

#[get("/list")]
async fn list() -> &'static str { "ok" }

fn main() {
    App::new()
        .service(health)
        .service(web::scope("/api").service(list));
}
"""
    assert _routes(tmp_path, code) == [("GET", "/api/list"), ("GET", "/health")]


def test_attribute_route_in_a_configure_function_keeps_the_unapplied_prefix_note(tmp_path):
    code = """
use actix_web::{get, web};

#[get("/list")]
async fn list() -> &'static str { "ok" }

pub fn init(cfg: &mut web::ServiceConfig) {
    cfg.service(list);
}
"""
    (tmp_path / "routes.rs").write_text(code)
    eps = map_api_endpoints(tmp_path)["endpoints"]
    assert [(e["path"], bool(e["note"])) for e in eps] == [("/list", True)]
