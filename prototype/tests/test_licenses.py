import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from veridion.licenses import categorize_license, check_dependency_licenses, detect_repo_license


def _mock_response(payload: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(payload).encode("utf-8")
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    return mock


def test_categorize_license_recognizes_permissive():
    assert categorize_license("MIT") == "permissive"
    assert categorize_license("Apache 2.0") == "permissive"
    assert categorize_license("BSD-3-Clause") == "permissive"
    assert categorize_license("ISC") == "permissive"


def test_categorize_license_recognizes_strong_copyleft():
    assert categorize_license("GPL v3") == "copyleft-strong"
    assert categorize_license("GNU General Public License v2") == "copyleft-strong"


def test_categorize_license_recognizes_agpl_before_generic_gpl_match():
    # "agpl" contains "gpl" as a literal substring - this only passes if AGPL is
    # checked before the generic GPL fallback, not incidentally.
    assert categorize_license("AGPL-3.0") == "copyleft-strong"


def test_categorize_license_recognizes_lgpl_before_generic_gpl_match():
    # same substring trap as AGPL - "lgpl" also contains "gpl" literally.
    assert categorize_license("LGPL-2.1") == "copyleft-weak"


def test_categorize_license_recognizes_mpl_as_weak_copyleft():
    assert categorize_license("MPL-2.0") == "copyleft-weak"


def test_categorize_license_unknown_for_none_or_unrecognized():
    assert categorize_license(None) == "unknown"
    assert categorize_license("") == "unknown"
    assert categorize_license("Some Custom Proprietary License") == "unknown"


def test_detect_repo_license_from_pyproject_toml_string_field(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\nlicense = "MIT"\n')

    result = detect_repo_license(repo)

    assert result["category"] == "permissive"
    assert "pyproject.toml" in result["detected_from"]


def test_detect_repo_license_from_pyproject_toml_table_field(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "x"\nlicense = { text = "Apache-2.0" }\n'
    )

    result = detect_repo_license(repo)

    assert result["category"] == "permissive"


def test_detect_repo_license_from_package_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(json.dumps({"license": "GPL-3.0"}))

    result = detect_repo_license(repo)

    assert result["category"] == "copyleft-strong"
    assert "package.json" in result["detected_from"]


def test_detect_repo_license_from_real_apache_license_file_text(tmp_path):
    # The exact real LICENSE file already committed at this repo's own root -
    # verifying against the actual file content, not a hand-typed guess at what
    # an Apache license file looks like.
    real_license = Path(__file__).resolve().parents[2] / "LICENSE"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "LICENSE").write_text(real_license.read_text(encoding="utf-8")[:2000])

    result = detect_repo_license(repo)

    assert result["category"] == "permissive"
    assert "LICENSE" in result["detected_from"]


def test_detect_repo_license_from_mit_license_file_text(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "LICENSE").write_text(
        "MIT License\n\n"
        "Copyright (c) 2026 Example\n\n"
        'Permission is hereby granted, free of charge, to any person obtaining a copy...\n'
    )

    result = detect_repo_license(repo)

    assert result["category"] == "permissive"


def test_detect_repo_license_from_gpl_license_file_text(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "LICENSE").write_text(
        "                    GNU GENERAL PUBLIC LICENSE\n"
        "                       Version 3, 29 June 2007\n"
    )

    result = detect_repo_license(repo)

    assert result["category"] == "copyleft-strong"


def test_detect_repo_license_unknown_when_nothing_present(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    result = detect_repo_license(repo)

    assert result == {"category": "unknown", "detected_from": None}


def test_check_dependency_licenses_no_pins_short_circuits_without_network_call(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    with patch("veridion.licenses.urllib.request.urlopen") as mock_urlopen:
        result = check_dependency_licenses(repo)

    mock_urlopen.assert_not_called()
    assert result["checked"] is True
    assert result["findings"] == []


def test_check_dependency_licenses_reports_a_copyleft_pypi_dependency(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("pyqt5==5.15.10\n")

    response = _mock_response({"info": {"license": "GPL v3", "classifiers": []}})

    with patch("veridion.licenses.urllib.request.urlopen", return_value=response):
        result = check_dependency_licenses(repo)

    assert result["checked"] is True
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["package"] == "pyqt5"
    assert finding["ecosystem"] == "PyPI"
    assert finding["license"] == "GPL v3"
    assert finding["category"] == "copyleft-strong"


def test_check_dependency_licenses_omits_permissive_dependencies_from_findings(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("requests==2.31.0\n")

    response = _mock_response({"info": {"license": "Apache 2.0", "classifiers": []}})

    with patch("veridion.licenses.urllib.request.urlopen", return_value=response):
        result = check_dependency_licenses(repo)

    assert result["findings"] == []


def test_check_dependency_licenses_reports_an_npm_dependency(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(json.dumps({"dependencies": {"some-gpl-lib": "1.0.0"}}))

    response = _mock_response({"license": "GPL-3.0"})

    with patch("veridion.licenses.urllib.request.urlopen", return_value=response):
        result = check_dependency_licenses(repo)

    assert len(result["findings"]) == 1
    assert result["findings"][0]["ecosystem"] == "npm"
    assert result["findings"][0]["category"] == "copyleft-strong"


def test_check_dependency_licenses_falls_back_to_classifiers_when_license_field_is_generic(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("somepkg==1.0.0\n")

    response = _mock_response(
        {
            "info": {
                "license": "UNKNOWN",
                "classifiers": ["License :: OSI Approved :: GNU General Public License v3 (GPLv3)"],
            }
        }
    )

    with patch("veridion.licenses.urllib.request.urlopen", return_value=response):
        result = check_dependency_licenses(repo)

    assert len(result["findings"]) == 1
    assert result["findings"][0]["category"] == "copyleft-strong"


def test_check_dependency_licenses_degrades_gracefully_when_one_lookup_fails(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("fastapi==0.100.0\n")

    with patch(
        "veridion.licenses.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = check_dependency_licenses(repo)

    # A per-package lookup failure is not the same as the whole check being
    # unreachable - it's reported as a finding with an "unknown" category rather
    # than silently vanishing or failing the whole check.
    assert result["checked"] is True
    assert len(result["findings"]) == 1
    assert result["findings"][0]["category"] == "unknown"
    assert result["findings"][0]["license"] is None


def test_check_dependency_licenses_includes_repo_license(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(json.dumps({"license": "MIT"}))

    result = check_dependency_licenses(repo)

    assert result["repo_license"]["category"] == "permissive"
