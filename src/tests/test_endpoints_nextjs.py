from aletheore.endpoints import map_api_endpoints


def _routes(tmp_path, files, next_project=True):
    if next_project and not any(n.endswith("package.json") for n in files):
        (tmp_path / "package.json").write_text('{"dependencies": {"next": "15.0.0"}}')
    for name, code in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code)
    return sorted((e["method"], e["path"]) for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == "nextjs")


def test_app_router_route_handlers(tmp_path):
    files = {
        "app/api/users/route.ts": "export async function GET(req) {}\nexport const POST = async (req) => {}\n",
        "src/app/api/users/[id]/route.js": "export function DELETE() {}\nexport { PUT }\nfunction PUT() {}\n",
    }
    assert _routes(tmp_path, files) == [
        ("DELETE", "/api/users/{id}"), ("GET", "/api/users"), ("POST", "/api/users"), ("PUT", "/api/users/{id}"),
    ]


def test_route_groups_slots_and_catch_all_segments(tmp_path):
    files = {
        "app/(shop)/api/items/[...slug]/route.ts": "export function GET() {}\n",
        "app/@modal/api/x/route.ts": "export function GET() {}\n",
        "app/api/docs/[[...path]]/route.ts": "export function GET() {}\n",
    }
    assert _routes(tmp_path, files) == [("GET", "/api/docs/{...path?}"), ("GET", "/api/items/{...slug}"), ("GET", "/api/x")]


def test_pages_router_api_files_answer_any_method(tmp_path):
    files = {
        "pages/api/items.js": "export default function handler(req, res) {}\n",
        "pages/api/users/index.ts": "export default async function h(req, res) {}\n",
        "pages/api/posts/[id].ts": "export default function h(req, res) {}\n",
        "pages/about.tsx": "export default function About() {}\n",
    }
    assert _routes(tmp_path, files) == [("ANY", "/api/items"), ("ANY", "/api/posts/{id}"), ("ANY", "/api/users")]


def test_route_file_with_no_http_exports_and_non_route_files_are_ignored(tmp_path):
    files = {"app/api/a/route.ts": "export const dynamic = 'force-dynamic'\n", "app/page.tsx": "export default function P() {}\n"}
    assert _routes(tmp_path, files) == []


def test_app_folders_outside_a_next_project_are_not_routes(tmp_path):
    files = {"app/api/x/route.ts": "export function GET() {}\n", "pages/api/y.js": "export default function h() {}\n"}
    assert _routes(tmp_path, files, next_project=False) == []


def test_nested_app_or_pages_folders_inside_the_project_are_not_the_router_root(tmp_path):
    files = {"lib/app/users/route.ts": "export function GET() {}\n", "components/pages/api/users.ts": "export default function h() {}\n"}
    assert _routes(tmp_path, files) == []


def test_monorepo_package_with_its_own_next_dependency(tmp_path):
    files = {
        "apps/web/package.json": '{"dependencies": {"next": "15"}}',
        "apps/web/app/api/a/route.ts": "export function GET() {}\n",
        "apps/docs/app/api/b/route.ts": "export function GET() {}\n",
    }
    assert _routes(tmp_path, files, next_project=False) == [("GET", "/api/a")]


def test_commented_out_exports_are_not_methods_and_pages_routes_have_a_line(tmp_path):
    files = {
        "app/api/a/route.ts": "// export async function DELETE() {}\n/* export const PUT = 1 */\nexport function GET() {}\n",
        "pages/api/items.js": "const x = 1\n\nexport default function handler(req, res) {}\n",
    }
    assert _routes(tmp_path, files) == [("ANY", "/api/items"), ("GET", "/api/a")]
    lines = {e["path"]: e["line"] for e in map_api_endpoints(tmp_path)["endpoints"]}
    assert lines["/api/items"] == 3 and lines["/api/a"] == 3
