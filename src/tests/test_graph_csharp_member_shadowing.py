"""A simple name can bind to an inherited or own member instead of a type of the same name (Roslyn binds the member)."""

from pathlib import Path

from aletheore.scanner.graph import build_module_graph


def _imports(tmp_path: Path, files: dict[str, str], path: str) -> set[str]:
    repo = tmp_path / "r"
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    modules, _, _ = build_module_graph(repo)
    return set(next(m for m in modules if m["path"] == path)["imports"])


MAPPER = "namespace App;\npublic class Mapper { public void Map() {} }\n"
IMAPPER = "namespace App;\npublic interface IMapper { void Map(); }\n"


def test_name_matching_a_type_still_links_without_a_shadowing_member(tmp_path):
    files = {
        "Mapper.cs": MAPPER,
        "T.cs": "namespace App;\npublic class T { void Run() { Mapper.Map(); } }\n",
    }
    assert "Mapper.cs" in _imports(tmp_path, files, "T.cs")


def test_inherited_property_named_like_a_type_shadows_it(tmp_path):
    files = {
        "Mapper.cs": MAPPER,
        "IMapper.cs": IMAPPER,
        "Base.cs": "namespace App;\npublic class Base { protected IMapper Mapper { get; set; } }\n",
        "T.cs": "namespace App;\npublic class T : Base { void Run() { Mapper.Map(); } }\n",
    }
    assert "Mapper.cs" not in _imports(tmp_path, files, "T.cs")
    assert "Base.cs" in _imports(tmp_path, files, "T.cs")


def test_member_declared_two_bases_up_still_shadows(tmp_path):
    files = {
        "Mapper.cs": MAPPER,
        "IMapper.cs": IMAPPER,
        "Root.cs": "namespace App;\npublic class Root { protected IMapper Mapper; }\n",
        "Mid.cs": "namespace App;\npublic class Mid : Root { }\n",
        "T.cs": "namespace App;\npublic class T : Mid { void Run() { Mapper.Map(); } }\n",
    }
    assert "Mapper.cs" not in _imports(tmp_path, files, "T.cs")


def test_own_field_named_like_a_type_shadows_it(tmp_path):
    files = {
        "Mapper.cs": MAPPER,
        "IMapper.cs": IMAPPER,
        "T.cs": "namespace App;\npublic class T { IMapper Mapper = null; void Run() { Mapper.Map(); } }\n",
    }
    assert "Mapper.cs" not in _imports(tmp_path, files, "T.cs")


def test_member_of_an_unrelated_class_does_not_shadow(tmp_path):
    files = {
        "Mapper.cs": MAPPER,
        "IMapper.cs": IMAPPER,
        "Other.cs": "namespace App;\npublic class Other { public IMapper Mapper { get; } }\n",
        "T.cs": "namespace App;\npublic class T { void Run() { Mapper.Map(); } }\n",
    }
    assert "Mapper.cs" in _imports(tmp_path, files, "T.cs")


def test_member_whose_type_has_the_same_name_keeps_the_type_edge(tmp_path):
    # "Color Color": `Mapper Mapper` - the name binds to the member or the type depending on use, so the type stays linked.
    files = {
        "Mapper.cs": MAPPER,
        "T.cs": "namespace App;\npublic class T { public Mapper Mapper { get; } void Run() { Mapper.Map(); } }\n",
    }
    assert "Mapper.cs" in _imports(tmp_path, files, "T.cs")


def test_a_type_position_use_is_kept_even_if_a_member_shares_the_name(tmp_path):
    files = {
        "Mapper.cs": MAPPER,
        "IMapper.cs": IMAPPER,
        "Base.cs": "namespace App;\npublic class Base { protected IMapper Mapper { get; set; } }\n",
        "T.cs": "namespace App;\npublic class T : Base { void Run() { var m = new Mapper(); Mapper.Map(); } }\n",
    }
    assert "Mapper.cs" in _imports(tmp_path, files, "T.cs")


def test_same_named_own_type_in_another_namespace_does_not_shadow(tmp_path):
    # N2.T has a Mapper property; N1.T does not, so inside N1.T `Mapper` is the type
    files = {
        "Mapper.cs": MAPPER,
        "IMapper.cs": IMAPPER,
        "N1/T.cs": "namespace App.N1;\npublic class T { void Run() { Mapper.Map(); } }\n",
        "N2/T.cs": "namespace App.N2;\npublic class T { public IMapper Mapper { get; set; } }\n",
    }
    assert "Mapper.cs" in _imports(tmp_path, files, "N1/T.cs")


def test_member_of_only_one_of_two_same_named_bases_does_not_shadow(tmp_path):
    files = {
        "Mapper.cs": MAPPER,
        "IMapper.cs": IMAPPER,
        "A/Base.cs": "namespace App.A;\npublic class Base { protected IMapper Mapper { get; set; } }\n",
        "B/Base.cs": "namespace App.B;\npublic class Base { }\n",
        "T.cs": "namespace App;\nusing App.B;\npublic class T : Base { void Run() { Mapper.Map(); } }\n",
    }
    assert "Mapper.cs" in _imports(tmp_path, files, "T.cs")
