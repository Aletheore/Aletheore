import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from aletheore.vulnerabilities import check_vulnerabilities


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("fastapi==0.100.0\nrequests>=2.0\n# comment\n")
    (repo / "package.json").write_text(
        json.dumps({"dependencies": {"left-pad": "^1.3.0"}, "devDependencies": {}})
    )
    return repo


def _mock_response(payload: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(payload).encode("utf-8")
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    return mock


def test_check_vulnerabilities_parses_pinned_pip_and_npm_versions(tmp_path):
    repo = make_repo(tmp_path)
    batch_response = _mock_response({"results": [{}, {}]})

    with patch("aletheore.vulnerabilities.urllib.request.urlopen", return_value=batch_response) as mock_urlopen:
        result = check_vulnerabilities(repo)

    assert result == {"checked": True, "reason": None, "findings": []}
    sent_request = mock_urlopen.call_args[0][0]
    sent_body = json.loads(sent_request.data)
    queries = sent_body["queries"]
    assert {"package": {"name": "fastapi", "ecosystem": "PyPI"}, "version": "0.100.0"} in queries
    assert {"package": {"name": "left-pad", "ecosystem": "npm"}, "version": "1.3.0"} in queries
    assert not any(q["package"]["name"] == "requests" for q in queries)


def test_check_vulnerabilities_reports_a_real_finding(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("fastapi==0.100.0\n")

    batch_response = _mock_response({"results": [{"vulns": [{"id": "PYSEC-2024-38"}]}]})
    detail_response = _mock_response(
        {
            "id": "PYSEC-2024-38",
            "details": "ReDoS in multipart form parsing.",
            "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H"}],
        }
    )

    with patch(
        "aletheore.vulnerabilities.urllib.request.urlopen",
        side_effect=[batch_response, detail_response],
    ):
        result = check_vulnerabilities(repo)

    assert result["checked"] is True
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["package"] == "fastapi"
    assert finding["advisory_id"] == "PYSEC-2024-38"
    assert finding["severity"] == [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H"}]


def test_check_vulnerabilities_degrades_gracefully_on_network_failure(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("fastapi==0.100.0\n")

    with patch(
        "aletheore.vulnerabilities.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = check_vulnerabilities(repo)

    assert result["checked"] is False
    assert "connection refused" in result["reason"]
    assert result["findings"] == []


def test_check_vulnerabilities_no_pins_short_circuits_without_network_call(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    with patch("aletheore.vulnerabilities.urllib.request.urlopen") as mock_urlopen:
        result = check_vulnerabilities(repo)

    mock_urlopen.assert_not_called()
    assert result == {"checked": True, "reason": None, "findings": []}


def test_parse_go_pins_reads_require_block(tmp_path):
    from aletheore.vulnerabilities import _parse_go_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text(
        "module example.com/thing\n\n"
        "go 1.21\n\n"
        "require (\n"
        "\tgithub.com/gin-gonic/gin v1.9.0\n"
        "\tgithub.com/pkg/errors v0.9.1 // indirect\n"
        ")\n"
    )

    pins = _parse_go_pins(repo)

    assert ("github.com/gin-gonic/gin", "v1.9.0", "Go") in pins
    assert ("github.com/pkg/errors", "v0.9.1", "Go") in pins


def test_parse_go_pins_reads_single_line_require(tmp_path):
    from aletheore.vulnerabilities import _parse_go_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text(
        "module example.com/thing\n\nrequire github.com/gin-gonic/gin v1.9.0\n"
    )

    pins = _parse_go_pins(repo)

    assert pins == [("github.com/gin-gonic/gin", "v1.9.0", "Go")]


def test_parse_go_pins_empty_when_no_go_mod(tmp_path):
    from aletheore.vulnerabilities import _parse_go_pins

    repo = tmp_path / "repo"
    repo.mkdir()

    assert _parse_go_pins(repo) == []


def test_check_vulnerabilities_includes_go_pins(tmp_path):
    from aletheore.vulnerabilities import check_vulnerabilities

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module x\n\nrequire github.com/gin-gonic/gin v1.9.0\n")

    mock = MagicMock()
    mock.read.return_value = b'{"results": [{}]}'
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False

    with patch("aletheore.vulnerabilities.urllib.request.urlopen", return_value=mock) as mock_urlopen:
        result = check_vulnerabilities(repo)

    assert result == {"checked": True, "reason": None, "findings": []}
    sent_body = json.loads(mock_urlopen.call_args[0][0].data)
    assert {
        "package": {"name": "github.com/gin-gonic/gin", "ecosystem": "Go"},
        "version": "v1.9.0",
    } in sent_body["queries"]


def test_parse_cargo_pins_reads_package_tables(tmp_path):
    from aletheore.vulnerabilities import _parse_cargo_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Cargo.lock").write_text(
        '# This file is automatically @generated by Cargo.\n'
        "version = 3\n\n"
        "[[package]]\n"
        'name = "serde"\n'
        'version = "1.0.219"\n'
        'source = "registry+https://github.com/rust-lang/crates.io-index"\n\n'
        "[[package]]\n"
        'name = "libc"\n'
        'version = "0.2.169"\n'
    )

    pins = _parse_cargo_pins(repo)

    assert ("serde", "1.0.219", "crates.io") in pins
    assert ("libc", "0.2.169", "crates.io") in pins


def test_parse_cargo_pins_empty_when_no_cargo_lock(tmp_path):
    from aletheore.vulnerabilities import _parse_cargo_pins

    repo = tmp_path / "repo"
    repo.mkdir()

    assert _parse_cargo_pins(repo) == []


def test_parse_maven_pins_reads_direct_dependencies(tmp_path):
    from aletheore.vulnerabilities import _parse_maven_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pom.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
        "  <dependencies>\n"
        "    <dependency>\n"
        "      <groupId>org.springframework</groupId>\n"
        "      <artifactId>spring-core</artifactId>\n"
        "      <version>6.1.14</version>\n"
        "    </dependency>\n"
        "    <dependency>\n"
        "      <groupId>com.example</groupId>\n"
        "      <artifactId>interpolated</artifactId>\n"
        "      <version>${some.property}</version>\n"
        "    </dependency>\n"
        "  </dependencies>\n"
        "</project>\n"
    )

    pins = _parse_maven_pins(repo)

    assert ("org.springframework:spring-core", "6.1.14", "Maven") in pins
    assert not any(p[0] == "com.example:interpolated" for p in pins)


def test_parse_maven_pins_empty_when_no_pom(tmp_path):
    from aletheore.vulnerabilities import _parse_maven_pins

    repo = tmp_path / "repo"
    repo.mkdir()

    assert _parse_maven_pins(repo) == []


def test_parse_gemfile_lock_pins_reads_gem_specs_only(tmp_path):
    from aletheore.vulnerabilities import _parse_gemfile_lock_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Gemfile.lock").write_text(
        "PATH\n"
        "  remote: .\n"
        "  specs:\n"
        "    mygem (1.0.0)\n"
        "      activesupport (= 8.0.0)\n\n"
        "GEM\n"
        "  remote: https://rubygems.org/\n"
        "  specs:\n"
        "    activesupport (8.0.0)\n"
        "      base64\n"
        "    nokogiri (1.16.7)\n"
        "      mini_portile2 (~> 2.8.2)\n\n"
        "PLATFORMS\n"
        "  ruby\n"
    )

    pins = _parse_gemfile_lock_pins(repo)

    assert ("activesupport", "8.0.0", "RubyGems") in pins
    assert ("nokogiri", "1.16.7", "RubyGems") in pins
    assert not any(p[0] == "mygem" for p in pins)
    assert not any(p[0] == "base64" for p in pins)
    assert not any(p[0] == "mini_portile2" for p in pins)


def test_parse_gemfile_lock_pins_empty_when_no_gemfile_lock(tmp_path):
    from aletheore.vulnerabilities import _parse_gemfile_lock_pins

    repo = tmp_path / "repo"
    repo.mkdir()

    assert _parse_gemfile_lock_pins(repo) == []


def test_parse_composer_pins_reads_packages_array(tmp_path):
    from aletheore.vulnerabilities import _parse_composer_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "composer.lock").write_text(
        json.dumps(
            {
                "packages": [
                    {"name": "laravel/framework", "version": "v11.30.0"},
                    {"name": "symfony/console", "version": "v7.1.6"},
                ]
            }
        )
    )

    pins = _parse_composer_pins(repo)

    assert ("laravel/framework", "11.30.0", "Packagist") in pins
    assert ("symfony/console", "7.1.6", "Packagist") in pins


def test_parse_composer_pins_empty_when_no_composer_lock(tmp_path):
    from aletheore.vulnerabilities import _parse_composer_pins

    repo = tmp_path / "repo"
    repo.mkdir()

    assert _parse_composer_pins(repo) == []


def test_parse_nuget_pins_reads_resolved_versions(tmp_path):
    from aletheore.vulnerabilities import _parse_nuget_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "packages.lock.json").write_text(
        json.dumps(
            {
                "version": 1,
                "dependencies": {
                    "net8.0": {
                        "Newtonsoft.Json": {"type": "Direct", "resolved": "13.0.3"},
                        "Serilog": {"type": "Direct", "resolved": "4.0.1"},
                    }
                },
            }
        )
    )

    pins = _parse_nuget_pins(repo)

    assert ("Newtonsoft.Json", "13.0.3", "NuGet") in pins
    assert ("Serilog", "4.0.1", "NuGet") in pins


def test_parse_nuget_pins_empty_when_no_lock_file(tmp_path):
    from aletheore.vulnerabilities import _parse_nuget_pins

    repo = tmp_path / "repo"
    repo.mkdir()

    assert _parse_nuget_pins(repo) == []


def test_parse_pip_pins_reads_pep621_pyproject_dependencies(tmp_path):
    from aletheore.vulnerabilities import _parse_pip_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[project]\n"
        "dependencies = [\n"
        '  "asgiref>=3.12.1",\n'
        '  "sqlparse==0.5.0",\n'
        "  \"tzdata; sys_platform == 'win32'\",\n"
        "]\n"
    )

    pins = _parse_pip_pins(repo)

    assert ("asgiref", "3.12.1", "PyPI") in pins
    assert ("sqlparse", "0.5.0", "PyPI") in pins
    assert not any(pin[0] == "tzdata" for pin in pins)


def test_parse_pip_pins_reads_poetry_dependencies(tmp_path):
    from aletheore.vulnerabilities import _parse_pip_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[tool.poetry.dependencies]\n"
        'python = "^3.12"\n'
        'django = "^5.1.0"\n'
        'requests = { version = ">=2.32.0", optional = true }\n'
    )

    pins = _parse_pip_pins(repo)

    assert ("django", "5.1.0", "PyPI") in pins
    assert ("requests", "2.32.0", "PyPI") in pins
    assert not any(pin[0] == "python" for pin in pins)


def test_parse_pip_pins_is_additive_for_requirements_and_pyproject(tmp_path):
    from aletheore.vulnerabilities import _parse_pip_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("fastapi==0.100.0\n")
    (repo / "pyproject.toml").write_text('[project]\ndependencies = ["asgiref>=3.12.1"]\n')

    pins = _parse_pip_pins(repo)

    assert ("fastapi", "0.100.0", "PyPI") in pins
    assert ("asgiref", "3.12.1", "PyPI") in pins


def test_parse_npm_pins_prefers_package_lock_resolved_versions(tmp_path):
    from aletheore.vulnerabilities import _parse_npm_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(json.dumps({"dependencies": {"left-pad": "^1.0.0"}}))
    (repo / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "": {"dependencies": {"left-pad": "^1.0.0"}},
                    "node_modules/left-pad": {"version": "1.3.0"},
                }
            }
        )
    )

    assert _parse_npm_pins(repo) == [("left-pad", "1.3.0", "npm")]


def test_parse_cargo_pins_falls_back_to_cargo_toml_when_no_lockfile(tmp_path):
    from aletheore.vulnerabilities import _parse_cargo_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Cargo.toml").write_text(
        "[dependencies]\n"
        'serde = "1.0.219"\n'
        'local-crate = { path = "../local" }\n'
        "[dev-dependencies]\n"
        'tokio = { version = "^1.43.0", features = ["rt"] }\n'
    )

    pins = _parse_cargo_pins(repo)

    assert ("serde", "1.0.219", "crates.io") in pins
    assert ("tokio", "1.43.0", "crates.io") in pins
    assert not any(pin[0] == "local-crate" for pin in pins)


def test_parse_cargo_pins_prefers_lockfile_over_cargo_toml(tmp_path):
    from aletheore.vulnerabilities import _parse_cargo_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Cargo.toml").write_text('[dependencies]\nserde = "1.0.0"\n')
    (repo / "Cargo.lock").write_text('[[package]]\nname = "serde"\nversion = "1.0.219"\n')

    assert _parse_cargo_pins(repo) == [("serde", "1.0.219", "crates.io")]


def test_parse_maven_pins_resolves_properties_and_dependency_management(tmp_path):
    from aletheore.vulnerabilities import _parse_maven_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pom.xml").write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0">'
        "<properties><junit.version>5.10.2</junit.version></properties>"
        "<dependencyManagement><dependencies><dependency>"
        "<groupId>com.example</groupId><artifactId>managed</artifactId><version>1.2.3</version>"
        "</dependency></dependencies></dependencyManagement>"
        "<dependencies>"
        "<dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId>"
        "<version>${junit.version}</version></dependency>"
        "<dependency><groupId>com.example</groupId><artifactId>managed</artifactId></dependency>"
        "</dependencies>"
        "</project>"
    )

    pins = _parse_maven_pins(repo)

    assert ("org.junit.jupiter:junit-jupiter", "5.10.2", "Maven") in pins
    assert ("com.example:managed", "1.2.3", "Maven") in pins


def test_parse_maven_pins_reads_child_modules_and_skips_profile_dependencies(tmp_path):
    from aletheore.vulnerabilities import _parse_maven_pins

    repo = tmp_path / "repo"
    child = repo / "child"
    child.mkdir(parents=True)
    (repo / "pom.xml").write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0">'
        "<modules><module>child</module></modules>"
        "<profiles><profile><dependencies><dependency>"
        "<groupId>com.example</groupId><artifactId>profile-only</artifactId><version>9.9.9</version>"
        "</dependency></dependencies></profile></profiles>"
        "</project>"
    )
    (child / "pom.xml").write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0">'
        "<dependencies><dependency>"
        "<groupId>com.example</groupId><artifactId>child-lib</artifactId><version>1.0.0</version>"
        "</dependency></dependencies>"
        "</project>"
    )

    pins = _parse_maven_pins(repo)

    assert ("com.example:child-lib", "1.0.0", "Maven") in pins
    assert not any(pin[0] == "com.example:profile-only" for pin in pins)


def test_parse_gemfile_lock_pins_falls_back_to_gemspec_when_no_lockfile(tmp_path):
    from aletheore.vulnerabilities import _parse_gemfile_lock_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "demo.gemspec").write_text(
        'spec.add_dependency "rack", ">= 3.0.0"\n'
        'spec.add_runtime_dependency "thor", "~> 1.3.0"\n'
        'spec.add_dependency "activesupport", version\n'
    )

    pins = _parse_gemfile_lock_pins(repo)

    assert ("rack", "3.0.0", "RubyGems") in pins
    assert ("thor", "1.3.0", "RubyGems") in pins
    assert not any(pin[0] == "activesupport" for pin in pins)


def test_parse_gemfile_lock_pins_prefers_lockfile_over_gemspec(tmp_path):
    from aletheore.vulnerabilities import _parse_gemfile_lock_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "demo.gemspec").write_text('spec.add_dependency "rack", "1.0.0"\n')
    (repo / "Gemfile.lock").write_text("GEM\n  remote: https://rubygems.org/\n  specs:\n    rack (3.0.0)\n")

    assert _parse_gemfile_lock_pins(repo) == [("rack", "3.0.0", "RubyGems")]


def test_parse_composer_pins_falls_back_to_composer_json_when_no_lockfile(tmp_path):
    from aletheore.vulnerabilities import _parse_composer_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "composer.json").write_text(
        json.dumps({"require": {"php": "^8.3", "guzzlehttp/psr7": "^2.7.0"}})
    )

    assert _parse_composer_pins(repo) == [("guzzlehttp/psr7", "2.7.0", "Packagist")]


def test_parse_composer_pins_prefers_lockfile_over_composer_json(tmp_path):
    from aletheore.vulnerabilities import _parse_composer_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "composer.json").write_text(json.dumps({"require": {"guzzlehttp/psr7": "^2.0.0"}}))
    (repo / "composer.lock").write_text(
        json.dumps({"packages": [{"name": "guzzlehttp/psr7", "version": "2.7.1"}]})
    )

    assert _parse_composer_pins(repo) == [("guzzlehttp/psr7", "2.7.1", "Packagist")]


def test_parse_nuget_pins_falls_back_to_csproj_package_references(tmp_path):
    from aletheore.vulnerabilities import _parse_nuget_pins

    repo = tmp_path / "repo"
    app = repo / "src" / "App"
    app.mkdir(parents=True)
    (app / "App.csproj").write_text(
        "<Project><ItemGroup>"
        '<PackageReference Include="Serilog" Version="4.0.1" />'
        "</ItemGroup></Project>"
    )

    assert _parse_nuget_pins(repo) == [("Serilog", "4.0.1", "NuGet")]


def test_parse_nuget_pins_reads_central_package_management_versions(tmp_path):
    from aletheore.vulnerabilities import _parse_nuget_pins

    repo = tmp_path / "repo"
    app = repo / "src" / "App"
    app.mkdir(parents=True)
    (repo / "Directory.Packages.props").write_text(
        "<Project><ItemGroup>"
        '<PackageVersion Include="Newtonsoft.Json" Version="13.0.3" />'
        "</ItemGroup></Project>"
    )
    (app / "App.csproj").write_text(
        "<Project><ItemGroup>"
        '<PackageReference Include="Newtonsoft.Json" />'
        "</ItemGroup></Project>"
    )

    assert _parse_nuget_pins(repo) == [("Newtonsoft.Json", "13.0.3", "NuGet")]


def test_parse_nuget_pins_prefers_lockfile_over_project_files(tmp_path):
    from aletheore.vulnerabilities import _parse_nuget_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "App.csproj").write_text(
        '<Project><ItemGroup><PackageReference Include="Serilog" Version="4.0.0" /></ItemGroup></Project>'
    )
    (repo / "packages.lock.json").write_text(
        json.dumps({"dependencies": {"net8.0": {"Serilog": {"resolved": "4.0.1"}}}})
    )

    assert _parse_nuget_pins(repo) == [("Serilog", "4.0.1", "NuGet")]
