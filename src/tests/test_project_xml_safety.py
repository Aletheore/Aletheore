import pytest

from aletheore.scanner import graph


def test_a_normal_project_file_is_parsed(tmp_path):
    path = tmp_path / "App.csproj"
    path.write_text('<Project><ItemGroup><ProjectReference Include="..\\Lib\\Lib.csproj"/></ItemGroup></Project>')
    root = graph._parse_project_xml(path)
    assert [e.attrib["Include"] for e in root.iter("ProjectReference")] == ["..\\Lib\\Lib.csproj"]


@pytest.mark.parametrize(
    "body",
    [
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><Project>&b;</Project>',
        "<!doctype project><Project/>",
        '<!ENTITY x "y"><Project/>',
    ],
)
def test_a_project_file_that_declares_a_doctype_or_entity_is_refused(tmp_path, body):
    path = tmp_path / "Bad.csproj"
    path.write_text(body)
    with pytest.raises(ValueError):
        graph._parse_project_xml(path)


def test_an_implausibly_large_project_file_is_refused(tmp_path):
    path = tmp_path / "Big.csproj"
    path.write_text("<Project>" + " " * (graph._PROJECT_XML_MAX_BYTES + 1) + "</Project>")
    with pytest.raises(ValueError):
        graph._parse_project_xml(path)


def test_a_hostile_project_file_does_not_stop_the_scan(tmp_path):
    (tmp_path / "Bad.csproj").write_text('<!DOCTYPE x [<!ENTITY a "b">]><Project/>')
    (tmp_path / "A.cs").write_text("class A {}\n")
    index = graph._csharp_project_index(tmp_path, [tmp_path / "A.cs"])
    assert index is not None  # the bad file is treated as having no references; the scan carries on
