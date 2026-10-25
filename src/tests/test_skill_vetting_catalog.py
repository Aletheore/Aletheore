from aletheore import skill_hub, skill_vetting


def test_every_bundled_skill_passes_vetting():
    failures = {}
    checked = 0
    for skill_md_path in skill_hub._iter_bundled_skill_dirs():  # yields each */*/SKILL.md path
        checked += 1
        result = skill_vetting.vet_skill_directory(skill_md_path.parent)
        if result:
            failures[str(skill_md_path)] = result
    # The gate must not go vacuous: if the walk finds (almost) nothing, "no failures" proves nothing.
    assert checked >= 1000, f"vetting only looked at {checked} skills"
    assert failures == {}, failures
