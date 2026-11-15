from aletheore.endpoints import map_api_endpoints


def _entries(tmp_path, code):
    (tmp_path / "main.rs").write_text(code)
    return sorted((e["method"], e["path"], bool(e["note"])) for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == "rocket")


def test_mounted_routes_take_the_mount_prefix(tmp_path):
    code = '''
#[macro_use] extern crate rocket;
#[get("/users/<id>")]
fn user(id: u32) -> String { id.to_string() }
#[post("/users", data = "<body>")]
async fn create(body: String) {}
#[launch]
fn rocket() -> _ { rocket::build().mount("/api", routes![user, create]) }
'''
    assert _entries(tmp_path, code) == [("GET", "/api/users/<id>", False), ("POST", "/api/users", False)]


def test_route_attribute_form_and_unmounted_handler(tmp_path):
    code = '''
use rocket::route;
#[route(PUT, uri = "/thing")]
fn thing() {}
'''
    assert _entries(tmp_path, code) == [("PUT", "/thing", True)]


def test_handler_mounted_at_two_prefixes_is_listed_at_both(tmp_path):
    code = '''
#[get("/ping")]
fn ping() {}
fn build() { rocket::build().mount("/v1", routes![ping]).mount("/v2", routes![ping]); }
'''
    assert _entries(tmp_path, code) == [("GET", "/v1/ping", False), ("GET", "/v2/ping", False)]


def test_file_without_rocket_is_ignored(tmp_path):
    code = '#[get("/x")]\nfn x() {}\n'
    assert _entries(tmp_path, code) == []


def test_mount_in_another_file_prefixes_the_handler_of_that_module(tmp_path):
    (tmp_path / "src" / "routes").mkdir(parents=True)
    (tmp_path / "src" / "lib.rs").write_text(
        'use rocket;\nfn build() { rocket::build().mount(\n  "/api",\n  routes![routes::users::login, routes::articles::login]\n); }\n'
    )
    (tmp_path / "src" / "routes" / "users.rs").write_text('use rocket;\n#[post("/users/login")]\nfn login() {}\n')
    (tmp_path / "src" / "routes" / "other.rs").write_text('use rocket;\n#[post("/other/login")]\nfn login() {}\n')
    got = sorted((e["path"], bool(e["note"])) for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == "rocket")
    assert got == [("/api/users/login", False), ("/other/login", True)]


def test_query_part_of_the_route_is_not_part_of_the_path(tmp_path):
    code = '#[get("/articles?<limit>&<offset>")]\nfn list(limit: u8, offset: u8) {}\nfn b() { rocket::build().mount("/api", routes![list]); }\n'
    assert _entries(tmp_path, code) == [("GET", "/api/articles", False)]
