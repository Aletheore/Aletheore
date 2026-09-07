"""Rails model-to-model relation edges, derived directly from each model
file's own belongs_to/has_one/has_many/has_and_belongs_to_many
declarations - not from schema/migrations at all.

Why this exists: architecture.py's build_clusters groups files by import-
graph density (networkx greedy_modularity_communities), which only sees
edges from real import/require statements. A Rails model relates to
another model through a declarative ActiveRecord association
(`belongs_to :user`), never a literal `require` - so two files whose
tables share a real, obvious foreign-key relationship (Post belongs_to
User) produce zero edges in the import graph and cluster as unrelated
singletons. Confirmed real, not hypothetical: a real Discourse scan
(app/models + db, 382 files) produced near-one-cluster-per-file before
this module existed, because Rails models barely reference each other
via Ruby require/import at all, even though they are obviously related.

Scope, deliberately narrow, matching orm_migrations.py's own restraint:
- A file's class counts as a model if it inherits ActiveRecord::Base or
  ApplicationRecord directly, or transitively through a chain of other
  classes defined among this same set of files (e.g. a real Discourse
  case: ReviewablePost < Reviewable < ActiveRecord::Base) - the chain is
  walked with a cycle guard, and a superclass that isn't itself one of
  these files' own classes stops the walk rather than guessing whether it
  ultimately reaches ActiveRecord::Base.
- The class definition itself may be wrapped in one or more `module`
  blocks (namespaced models, e.g. `module Admin; class User < AR::Base;
  end; end` - a real, common Rails pattern) - resolution still keys on
  the class's own short name, same as an unwrapped class, so two
  namespaced models sharing a short name (`Admin::User` vs `Api::User`)
  are as best-effort-resolved as two unnamespaced files already were.
- Only belongs_to/has_one/has_many/has_and_belongs_to_many are read; any
  other method call is ignored.
- An explicit class_name: option always wins over the naming convention.
- A polymorphic belongs_to (polymorphic: true) or a has_many/has_one with
  as: (also polymorphic) is skipped - the association's real target
  varies row by row, not one fixed model, so there is no single file to
  point an edge at.
- A :through association is skipped - its real target is a second-degree
  relation via the through model, not something this file's own
  association name resolves to directly, and resolving it correctly
  needs the through model's own associations too, not just this file's.
- Naming-convention resolution (when there is no explicit class_name:)
  reuses orm_migrations.py's own _pluralize heuristic (regular nouns
  only - irregular plurals like person/people resolve wrong) rather than
  adding a second, differently-imperfect inflector, and only ever
  resolves against class names actually found among this scan's own
  model files - never a guessed file that may not exist.
"""

from __future__ import annotations

import re

from aletheore.orm_migrations import (
    _pluralize,
    _rb_args,
    _rb_bool_kwarg,
    _rb_call_name,
    _rb_kwarg,
    _rb_parser,
    _rb_symbol_text,
    _rb_text,
)

_CAMEL_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _snake_case(class_name: str) -> str:
    """CamelCase class name -> snake_case (e.g. "PostReply" -> "post_reply"),
    matching Rails' own real inflector convention for this direction (no
    irregular cases here - underscore.rb's camel-to-snake rule is
    mechanical, unlike pluralization)."""
    return _CAMEL_BOUNDARY_RE.sub("_", class_name).lower()

_ASSOCIATION_METHODS = {"belongs_to", "has_one", "has_many", "has_and_belongs_to_many"}
_PLURAL_METHODS = {"has_many", "has_and_belongs_to_many"}
_MODEL_SUPERCLASSES = {"ActiveRecord::Base", "ApplicationRecord"}


def _first_class_node(node):
    """The first class definition reachable from `node` without crossing
    into a class/method body - so a model wrapped in one or more `module`
    blocks (a common real Rails namespacing pattern, e.g. `module Admin;
    class User < ApplicationRecord; end; end`) is still found, while a
    class nested inside another class/method's body is not mistaken for a
    top-level definition."""
    for child in node.children:
        if child.type == "class":
            return child
        if child.type == "module":
            body = child.child_by_field_name("body")
            if body is not None:
                found = _first_class_node(body)
                if found is not None:
                    return found
    return None


def _class_name_and_superclass(source: bytes) -> tuple[str, str] | None:
    """(class name, superclass text) for this file's first class
    definition - at the top level or nested in one or more `module`
    blocks - or None if the file has no such class definition at all."""
    try:
        tree = _rb_parser().parse(source)
    except Exception:  # noqa: BLE001 - malformed/truncated source, never a guess
        return None
    node = _first_class_node(tree.root_node)
    if node is None:
        return None
    name_node = node.child_by_field_name("name")
    super_node = node.child_by_field_name("superclass")
    if name_node is None or super_node is None:
        return None
    name = _rb_text(name_node, source)
    superclass = _rb_text(super_node, source).lstrip("<").strip()
    return name, superclass


def _resolve_model_class_names(model_files: dict[str, bytes]) -> dict[str, str]:
    """path -> class name, for every file whose class inherits
    ActiveRecord::Base/ApplicationRecord directly, or transitively through
    a chain of other classes also defined among these same files."""
    direct: dict[str, tuple[str, str]] = {}
    superclass_by_class_name: dict[str, str] = {}
    for path, source in model_files.items():
        found = _class_name_and_superclass(source)
        if found is None:
            continue
        name, superclass = found
        direct[path] = (name, superclass)
        superclass_by_class_name[name] = superclass

    resolved: dict[str, bool] = {}

    def _is_ar_model(class_name: str, visited: frozenset[str]) -> bool:
        if class_name in _MODEL_SUPERCLASSES:
            return True
        if class_name in resolved:
            return resolved[class_name]
        # Cycle guard: a class can only appear once per walk - two classes
        # inheriting from each other (malformed, but not this module's job
        # to reject) must not recurse forever.
        if class_name in visited:
            return False
        parent = superclass_by_class_name.get(class_name)
        if parent is None:
            # Not one of this scan's own classes - genuinely can't tell
            # whether it ultimately reaches ActiveRecord::Base, so this
            # chain stops here rather than guessing.
            return False
        result = _is_ar_model(parent, visited | {class_name})
        resolved[class_name] = result
        return result

    return {
        path: name
        for path, (name, superclass) in direct.items()
        if superclass in _MODEL_SUPERCLASSES or _is_ar_model(superclass, frozenset())
    }


def _walk_association_calls(source: bytes):
    try:
        tree = _rb_parser().parse(source)
    except Exception:  # noqa: BLE001
        return
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "call":
            method = _rb_call_name(node, source)
            if method in _ASSOCIATION_METHODS:
                yield method, node
        stack.extend(node.children)


def _association_target_class(method: str, call_node, source: bytes) -> str | None:
    """The real target class name this one association points at, or None
    when it can't be resolved without guessing (polymorphic, :through, or
    a symbol-less call)."""
    args = _rb_args(call_node)
    if not args:
        return None
    if _rb_kwarg(args, "through", source) is not None:
        return None
    # Real gap found via Flash Review's own review of this module: a bare
    # presence check treated `polymorphic: false` - an explicit, valid
    # non-polymorphic declaration - the same as `polymorphic: true`,
    # skipping a real, resolvable association. Only a literal true value
    # means "no single fixed target"; false or a non-boolean value falls
    # through to normal resolution.
    if _rb_bool_kwarg(args, "polymorphic", source):
        return None
    if _rb_kwarg(args, "as", source) is not None:
        return None
    class_name_node = _rb_kwarg(args, "class_name", source)
    if class_name_node is not None:
        explicit = _rb_symbol_text(class_name_node, source)
        return explicit if explicit else None
    symbol_node = args[0]
    name = _rb_symbol_text(symbol_node, source)
    if not name:
        return None
    if method in _PLURAL_METHODS:
        # Reverses _pluralize's own regular-noun rule rather than adding a
        # separate singularizer: resolution below only accepts a match
        # against a real discovered model's pluralized name (see
        # rails_model_association_edges), so a wrong guess here simply
        # fails to match instead of pointing at the wrong file.
        return name
    return "".join(part.capitalize() for part in name.split("_"))


def rails_model_association_edges(model_files: dict[str, bytes]) -> list[tuple[str, str]]:
    """(file_a, file_b) edges between two of the given Rails model files
    whose classes are related by a real, resolvable belongs_to/has_one/
    has_many/has_and_belongs_to_many association - see this module's own
    docstring for exactly what counts as "resolvable". `model_files` maps
    a repo-relative path to that file's raw source bytes; a file with no
    directly-ActiveRecord-inheriting class is silently not a model and
    contributes no edges. Never returns an edge to a file not present in
    `model_files` - a real association whose target isn't part of this
    scan's own file set is not guessed at."""
    class_name_by_file = _resolve_model_class_names(model_files)
    file_by_class_name: dict[str, str] = {}
    file_by_plural_snake_name: dict[str, str] = {}
    for path, class_name in class_name_by_file.items():
        file_by_class_name[class_name] = path
        file_by_plural_snake_name[_pluralize(_snake_case(class_name))] = path

    edges: list[tuple[str, str]] = []
    for path, source in model_files.items():
        if path not in class_name_by_file:
            continue
        for method, call_node in _walk_association_calls(source):
            target = _association_target_class(method, call_node, source)
            if target is None:
                continue
            # Singular targets (belongs_to/has_one, already resolved to a
            # CamelCase class name) look up by class name directly; plural
            # targets (has_many/has_and_belongs_to_many, still the raw
            # snake_case symbol) look up by pluralized snake_case name -
            # never cross-checked against the other table, since a snake_case
            # symbol and a CamelCase class name never collide.
            target_file = file_by_class_name.get(target) or file_by_plural_snake_name.get(target)
            if target_file is None or target_file == path:
                continue
            edges.append((path, target_file))
    return edges
