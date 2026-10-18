from aletheore import skill_hub, skill_vetting


def test_every_bundled_skill_passes_vetting():
    failures = {}
    for skill_md_path in skill_hub._iter_bundled_skill_dirs():  # yields each */*/SKILL.md path
        result = skill_vetting.vet_skill_directory(skill_md_path.parent)
        if result:
            failures[str(skill_md_path)] = result
    assert failures == {}, failures
