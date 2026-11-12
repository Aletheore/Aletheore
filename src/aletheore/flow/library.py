"""Library mode: a package that others import has no request, so its public functions are the entry points and their
parameters are whatever the calling program passes (which can be request data). Findings from it are `minor`, because
whether a caller really passes untrusted data is outside the package.

`is_library(repo, lang)` is deliberately strict. An application (a web service, a command with a main) is analysed
through its routes and request sources alone; a polyglot repository is not treated as a library."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

# Classes whose "source" makes no sense for a function parameter.
SKIP_CWES = frozenset((79, 601, 501, 942))

SKIP_DIRS = frozenset((
    "test", "tests", "spec", "specs", "testing", "testdata", "fixtures", "example", "examples", "sample", "samples", "demo", "demos",
    "doc", "docs", "benchmark", "benchmarks", "bench", "scripts", "tools", "tool", "external", "third_party", "thirdparty", "vendor",
    "contrib", "e2e", "integration", "migrations", "build", "dist", "target",
))

_MANIFESTS = {
    "python": ("pyproject.toml", "setup.py", "setup.cfg"),
    "go": ("go.mod",),
    "java": ("pom.xml", "build.gradle", "build.gradle.kts"),
    "kotlin": ("build.gradle", "build.gradle.kts", "pom.xml"),
    "rust": ("Cargo.toml",),
    "ruby": (),
    "php": ("composer.json",),
    "csharp": (),
}

# A dependency on one of these means the repository is an application of that kind.
_WEB = {
    "python": r"flask|django|fastapi|starlette|tornado|aiohttp|sanic|pyramid|bottle|falcon|quart|streamlit|gradio|uvicorn|gunicorn|cherrypy|web2py|dash\b",
    "go": r"gin-gonic|labstack/echo|gofiber|go-chi|gorilla/mux|beego|revel|buffalo|kataras/iris|julienschmidt|emicklei/go-restful|gofr|goji|macaron|flamego|go-macaron",
    "java": r"spring-boot|spring-web|springframework|javax\.servlet|jakarta\.servlet|struts|dropwizard|micronaut|quarkus|vertx|jersey|resteasy|play-java|wicket|vaadin|tomcat|jetty|undertow|spark-core|javalin|ktor",
    "kotlin": r"spring-boot|springframework|javax\.servlet|jakarta\.servlet|ktor|micronaut|quarkus|vertx|javalin|http4k",
    "rust": r"actix|axum|rocket|warp|tide|poem|salvo|gotham|iron|nickel|tower-http|loco-rs|leptos|dioxus|yew|tauri",
    "php": r"laravel|symfony/framework|symfony/http-kernel|slim/slim|cakephp|codeigniter|yiisoft|zendframework|laminas/laminas-mvc|wordpress|drupal|magento",
    "ruby": r"rails|sinatra|hanami|grape|roda|padrino|cuba",
    "csharp": r"System\.Web|349C5851-65DF-11DA-9384-00065B846F21|Microsoft\.AspNetCore|Microsoft\.NET\.Sdk\.Web|Microsoft\.AspNet\.|Nancy|ServiceStack",
}

_OTHER_LANG_MANIFESTS = ("package.json", "Gemfile", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "composer.json", "Cargo.toml",
                         "requirements.txt", "pyproject.toml", "setup.py")


def _read(p: Path, limit: int = 400_000) -> str:
    try:
        return p.read_text(errors="replace")[:limit]
    except OSError:
        return ""


def _foreign_manifest(repo: Path, lang: str) -> bool:
    """A root manifest of another ecosystem: some other language's application with this one embedded in it."""
    own = set(_MANIFESTS.get(lang, ())) | {"requirements.txt"}
    if lang in ("java", "kotlin"):
        own |= {"build.gradle", "build.gradle.kts", "pom.xml", "settings.gradle", "settings.gradle.kts"}
    if lang == "python":
        own |= {"pyproject.toml", "setup.py", "setup.cfg"}
    return any((repo / m).exists() for m in _OTHER_LANG_MANIFESTS if m not in own)


@lru_cache(maxsize=64)
def is_library(repo: Path, lang: str) -> bool:
    if _foreign_manifest(repo, lang):
        return False
    web = re.compile(_WEB.get(lang, r"$^"), re.I)
    if lang == "python":
        texts = [_read(repo / m) for m in _MANIFESTS["python"] + ("requirements.txt", "Pipfile")]
        joined = "\n".join(texts)
        if not any(t for t in texts[:3]):
            return False  # nothing says this is a package
        return not web.search(joined)
    if lang == "go":
        mod = _read(repo / "go.mod")
        if not mod or web.search(mod):
            return False
        return not _go_has_main(repo)
    if lang in ("java", "kotlin"):
        build = "\n".join(_read(repo / m) for m in _MANIFESTS[lang])
        if not build.strip() or web.search(build):
            return False
        if re.search(r"<packaging>\s*war\s*</packaging>|id\s*[(\"']war[)\"']|application\s*\{|mainClass|Main-Class|spring", build):
            return False
        return True
    if lang == "rust":
        cargo = _read(repo / "Cargo.toml")
        if not cargo or web.search(cargo):
            return False
        return (repo / "src" / "lib.rs").exists() or "[lib]" in cargo
    if lang == "php":
        try:
            comp = json.loads(_read(repo / "composer.json"))
        except ValueError:
            return False
        if not isinstance(comp, dict):
            return False
        if comp.get("type") not in (None, "library"):
            return False
        deps = json.dumps({k: comp.get(k) for k in ("require", "require-dev")})
        return not web.search(deps) and bool(comp.get("autoload") or comp.get("name"))
    if lang == "ruby":
        gemspecs = list(repo.glob("*.gemspec"))
        if not gemspecs:
            return False
        return not web.search("\n".join(_read(g) for g in gemspecs) + "\n" + _read(repo / "Gemfile"))
    if lang == "csharp":
        projs = [p for p in repo.rglob("*.csproj") if not SKIP_DIRS.intersection(p.relative_to(repo).parts)][:40]
        if not projs:
            return False
        text = "\n".join(_read(p, 50_000) for p in projs)
        if web.search(text) or re.search(r"<OutputType>\s*(Exe|WinExe)", text):
            return False
        return True
    return False


def _go_has_main(repo: Path) -> bool:
    n = 0
    for p in repo.rglob("*.go"):
        if p.name.endswith("_test.go") or SKIP_DIRS.intersection(p.relative_to(repo).parts):
            continue
        n += 1
        if n > 4000:
            break
        head = _read(p, 4000)
        if re.search(r"^package main\b", head, re.M) and "func main(" in _read(p, 200_000):
            return True
    return False


def skipped_path(rel_parts: tuple[str, ...]) -> bool:
    return bool(SKIP_DIRS.intersection(rel_parts[:-1]))
