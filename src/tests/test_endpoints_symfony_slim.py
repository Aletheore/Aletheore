from aletheore.endpoints import map_api_endpoints


def _routes(tmp_path, code, framework, name="Ctl.php"):
    (tmp_path / name).write_text(code)
    return sorted((e["method"], e["path"]) for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == framework)


def test_symfony_class_prefix_and_method_routes(tmp_path):
    code = """<?php
namespace App\\Controller;
use Symfony\\Component\\Routing\\Attribute\\Route;
#[Route('/api/users')]
class UserController
{
    #[Route('/', name: 'list', methods: ['GET'])]
    public function list() {}
    #[Route('/{id}', methods: ['GET', 'PUT'])]
    public function one() {}
    #[Route(path: '/any')]
    public function any() {}
}
"""
    assert _routes(tmp_path, code, "symfony") == [
        ("ANY", "/api/users/any"), ("GET", "/api/users"), ("GET", "/api/users/{id}"), ("PUT", "/api/users/{id}"),
    ]


def test_symfony_route_without_a_class_prefix(tmp_path):
    code = "<?php\nuse Symfony\\Component\\Routing\\Attribute\\Route;\nclass C {\n    #[Route('/health', methods: ['GET'])]\n    public function h() {}\n}\n"
    assert _routes(tmp_path, code, "symfony") == [("GET", "/health")]


def test_slim_routes_and_nested_groups(tmp_path):
    code = """<?php
use Slim\\Factory\\AppFactory;
$app = AppFactory::create();
$app->get('/health', function ($req, $res) { return $res; });
$app->group('/api', function ($group) {
    $group->post('/items', 'ItemController:add');
    $group->group('/v2', function ($g) { $g->delete('/items/{id}', 'ItemController:remove'); });
});
"""
    assert _routes(tmp_path, code, "slim", "index.php") == [
        ("DELETE", "/api/v2/items/{id}"), ("GET", "/health"), ("POST", "/api/items"),
    ]


def test_php_without_either_framework_is_ignored(tmp_path):
    code = "<?php\n$app->get('/x', 'h');\n#[Route('/y')]\nclass A {}\n"
    assert _routes(tmp_path, code, "slim", "a.php") == [] and _routes(tmp_path, code, "symfony", "b.php") == []


def test_symfony_prefix_does_not_leak_into_the_next_class(tmp_path):
    code = """<?php
use Symfony\\Component\\Routing\\Attribute\\Route;
#[Route('/a')]
class A {
    #[Route('/x', methods: ['GET'])]
    public function x() {}
}
class B {
    #[Route('/y', methods: ['GET'])]
    public function y() {}
}
"""
    assert _routes(tmp_path, code, "symfony") == [("GET", "/a/x"), ("GET", "/y")]


def test_slim_config_lookups_are_not_routes(tmp_path):
    code = "<?php\nuse Slim\\App;\n$v = $settings->get('displayErrorDetails');\n$app->get('/real', 'h');\n"
    assert _routes(tmp_path, code, "slim", "index.php") == [("GET", "/real")]
