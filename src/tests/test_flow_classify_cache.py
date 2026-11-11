"""The classify cache must agree with the plain recursive definition after any mutation, however it is made."""
import random

from aletheore.flow.values import (
    SAFE, UNKNOWN, BuilderV, Choice, Const, ListV, MapV, Obj, Tainted, classify, classify_reference, join,
)

LEAVES = [Const("a"), Const(1), SAFE, UNKNOWN, Tainted("x"), Tainted("y", tid=3)]


def _containers(rng, n):
    out = []
    for _ in range(n):
        k = rng.choice("lmbo")
        out.append({"l": lambda: ListV([]), "m": lambda: MapV({}), "b": lambda: BuilderV([]), "o": lambda: Obj(fields={})}[k]())
    return out


def _pick(rng, pool):
    v = rng.choice(pool + LEAVES)
    if rng.random() < 0.15:
        v = join(v, rng.choice(pool + LEAVES))  # a Choice
    return v


def _mutate(rng, pool):
    c = rng.choice(pool)
    v = _pick(rng, pool)  # may point back at c or at an ancestor: cycles and shared sub-structure are intended
    if isinstance(c, ListV):
        rng.choice([lambda: c.items.append(v), lambda: c.items.extend([v, v]), lambda: c.items.insert(0, v),
                    lambda: c.items.pop() if c.items else None, lambda: c.items.__setitem__(0, v) if c.items else None,
                    lambda: c.items.clear(), lambda: setattr(c, "items", [v]), lambda: setattr(c, "opaque", rng.random() < .5),
                    lambda: c.items.__iadd__([v])])()
    elif isinstance(c, MapV):
        k = rng.choice("abc")
        rng.choice([lambda: c.entries.__setitem__(k, v), lambda: c.entries.update({k: v}), lambda: c.entries.pop(k, None),
                    lambda: c.entries.setdefault(k, v), lambda: c.entries.clear(), lambda: setattr(c, "entries", {k: v}),
                    lambda: setattr(c, "opaque", rng.random() < .5)])()
    elif isinstance(c, BuilderV):
        rng.choice([lambda: c.parts.append(v), lambda: c.parts.extend([v]), lambda: setattr(c, "parts", [v]),
                    lambda: setattr(c, "opaque", True), lambda: c.parts.clear()])()
    else:
        k = rng.choice("abc")
        rng.choice([lambda: c.fields.__setitem__(k, v), lambda: c.fields.update({k: v}), lambda: c.fields.pop(k, None),
                    lambda: setattr(c, "fields", {k: v}), lambda: c.fields.clear()])()


def test_cache_agrees_with_reference_under_random_mutation():
    for seed in range(40):
        rng = random.Random(seed)
        pool = _containers(rng, rng.randint(3, 9))
        for _ in range(120):
            _mutate(rng, pool)
            for c in rng.sample(pool, min(3, len(pool))):  # classify some, leave others cached across later mutations
                for d in (0, 3, 6):
                    assert classify(c, d) == classify_reference(c, d), (seed, d)
            v = _pick(rng, pool)
            assert classify(v) == classify_reference(v)


def test_taint_added_deep_inside_invalidates_a_cached_answer():
    inner = ListV([Const("a")])
    outer = MapV({"k": inner})
    assert classify(outer) == "safe"
    inner.items.append(Tainted("late"))
    assert classify(outer) == "tainted"
    inner.items.clear()
    assert classify(outer) == "safe"


def test_opaque_flag_and_rebinding_invalidate():
    lst = ListV([Const(1)])
    assert classify(lst) == "safe"
    lst.opaque = True
    assert classify(lst) == "unknown"
    lst.items = [Tainted("t")]
    assert classify(lst) == "tainted"


def test_obj_field_change_invalidates():
    o = Obj(fields={})
    assert classify(o) == "unknown"
    o.fields["a"] = Tainted("t")
    assert classify(o) == "tainted"
    o.fields["a"] = Const("c")
    assert classify(o) == "unknown"


def test_cycle_terminates_and_matches():
    a, b = ListV([]), ListV([])
    a.items.append(b)
    b.items.append(a)
    assert classify(a) == classify_reference(a) == "unknown"  # the walk hits the depth limit
    b.items.append(Tainted("t"))
    assert classify(a) == "tainted"


def test_choice_over_changing_containers():
    a, b = ListV([Const(1)]), ListV([Const(2)])
    ch = Choice(frozenset({a, b}))
    assert classify(ch) == "safe"
    b.items.append(Tainted("t"))
    assert classify(ch) == "tainted"
