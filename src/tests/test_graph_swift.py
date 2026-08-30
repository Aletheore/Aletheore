from pathlib import Path

from aletheore.scanner.graph import build_module_graph
from conftest import symbol_names


def make_swift_repo(tmp_path: Path) -> Path:
    # Mirrors a real SwiftPM package layout: one Package.swift manifest at
    # the root, each target's sources under Sources/<TargetName>/ (the
    # default SwiftPM convention when a target declares no explicit
    # `path:`). Handlers depends on both Store and Logging; App (the
    # executable target) depends on Handlers and Store directly.
    repo = tmp_path / "repo"
    sources = repo / "Sources"
    (sources / "Logging").mkdir(parents=True)
    (sources / "Store").mkdir(parents=True)
    (sources / "Handlers").mkdir(parents=True)
    (sources / "App").mkdir(parents=True)

    (repo / "Package.swift").write_text(
        "// swift-tools-version:5.9\n"
        "import PackageDescription\n\n"
        "let package = Package(\n"
        '    name: "ExampleApp",\n'
        "    targets: [\n"
        '        .target(name: "Logging"),\n'
        '        .target(name: "Store"),\n'
        '        .target(name: "Handlers", dependencies: ["Store", "Logging"]),\n'
        '        .executableTarget(name: "App", dependencies: ["Handlers", "Store"]),\n'
        "    ]\n"
        ")\n"
    )

    (sources / "Logging" / "Logger.swift").write_text(
        "/// Prefixes every message with a fixed tag.\n"
        "public class Logger {\n"
        "    private var prefix: String\n\n"
        "    public init(prefix: String) {\n"
        "        self.prefix = prefix\n"
        "    }\n\n"
        "    /// Prints `msg` prefixed with this logger's tag.\n"
        "    public func info(msg: String) {\n"
        '        print("\\(self.prefix): \\(msg)")\n'
        "    }\n"
        "}\n"
    )
    (sources / "Store" / "User.swift").write_text(
        "public struct User {\n"
        "    public var id: Int\n"
        "    public var name: String\n"
        "}\n"
    )
    (sources / "Store" / "Store.swift").write_text(
        "public class Store {\n"
        "    private var users: [Int: User] = [:]\n\n"
        "    public func get(id: Int) -> User? {\n"
        "        return users[id]\n"
        "    }\n"
        "}\n"
    )
    (sources / "Handlers" / "Handler.swift").write_text(
        "import Store\n"
        "import Logging\n\n"
        "public class Handler {\n"
        "    private var store: Store\n"
        "    private var logger: Logger\n\n"
        "    public init(store: Store, logger: Logger) {\n"
        "        self.store = store\n"
        "        self.logger = logger\n"
        "    }\n\n"
        "    public func getUser(id: Int) {\n"
        '        self.logger.info(msg: "fetching user")\n'
        "        let u = self.store.get(id: id)\n"
        "    }\n\n"
        "    private func internalOnly() {}\n"
        "}\n"
    )
    (sources / "App" / "Main.swift").write_text(
        "import Handlers\n"
        "import Store\n\n"
        "let store = Store()\n"
        'let logger = Logger(prefix: "server")\n'
        "let handler = Handler(store: store, logger: logger)\n"
        "handler.getUser(id: 1)\n"
    )
    return repo


def test_build_module_graph_extracts_swift_symbols(tmp_path):
    repo = make_swift_repo(tmp_path)
    modules, _dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    handler = by_path["Sources/Handlers/Handler.swift"]
    assert handler["language"] == "swift"
    assert "Handler" in symbol_names(handler["symbols"]["classes"])
    assert "getUser" in symbol_names(handler["symbols"]["functions"])

    get_user_fn = next(f for f in handler["symbols"]["functions"] if f["name"] == "getUser")
    assert get_user_fn["params"] == "(id: Int)"
    assert get_user_fn["is_public"] is True

    internal_fn = next(f for f in handler["symbols"]["functions"] if f["name"] == "internalOnly")
    assert internal_fn["is_public"] is False

    handler_cls = next(c for c in handler["symbols"]["classes"] if c["name"] == "Handler")
    assert handler_cls["params"] is None

    logger = by_path["Sources/Logging/Logger.swift"]
    info_fn = next(f for f in logger["symbols"]["functions"] if f["name"] == "info")
    assert info_fn["docstring"] == "Prints `msg` prefixed with this logger's tag."

    assert unparseable == []


def test_build_module_graph_swift_import_resolves_to_whole_target(tmp_path):
    # A Swift `import Store` names a whole compiled module, not one file -
    # unlike every per-file-resolving language (Python/JS/Go/Java), it must
    # fan out to every source file belonging to that target, the same way
    # Java's wildcard imports already do for a whole package.
    repo = make_swift_repo(tmp_path)
    _modules, dependency_graph, _unparseable = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("Sources/Handlers/Handler.swift", "Sources/Store/User.swift") in edges
    assert ("Sources/Handlers/Handler.swift", "Sources/Store/Store.swift") in edges
    assert ("Sources/Handlers/Handler.swift", "Sources/Logging/Logger.swift") in edges

    assert ("Sources/App/Main.swift", "Sources/Handlers/Handler.swift") in edges
    assert ("Sources/App/Main.swift", "Sources/Store/User.swift") in edges
    assert ("Sources/App/Main.swift", "Sources/Store/Store.swift") in edges


def test_build_module_graph_swift_target_path_override_from_package_manifest(tmp_path):
    # Package.swift can override a target's default Sources/<Name>/ location
    # via an explicit `path:` argument - a real SwiftPM feature, not an edge
    # case invented for this test (e.g. legacy repos migrating layouts).
    # Accuracy here means actually reading that override, not just assuming
    # the convention always holds.
    repo = tmp_path / "repo"
    custom = repo / "Vendor" / "StoreImpl"
    other = repo / "Sources" / "App"
    custom.mkdir(parents=True)
    other.mkdir(parents=True)

    (repo / "Package.swift").write_text(
        "// swift-tools-version:5.9\n"
        "import PackageDescription\n\n"
        "let package = Package(\n"
        '    name: "Ex",\n'
        "    targets: [\n"
        '        .target(name: "Store", path: "Vendor/StoreImpl"),\n'
        '        .executableTarget(name: "App", dependencies: ["Store"]),\n'
        "    ]\n"
        ")\n"
    )
    (custom / "User.swift").write_text("public struct User {\n    public var id: Int\n}\n")
    (other / "Main.swift").write_text("import Store\n\nlet u = User(id: 1)\n")

    _modules, dependency_graph, _unparseable = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("Sources/App/Main.swift", "Vendor/StoreImpl/User.swift") in edges
