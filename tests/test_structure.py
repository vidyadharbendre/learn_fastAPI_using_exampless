"""The repository's own contract.

These tests fail loudly if a day folder loses its README or the numbering gets
out of order — the kind of drift that creeps into a 21-part course otherwise.
"""

import re

import pytest

DAY_NAME = re.compile(r"^\d{2}_[a-z0-9]+(_[a-z0-9]+)*$")


@pytest.mark.structure
def test_there_are_exactly_21_days(day_dirs):
    assert len(day_dirs) == 21, [d.name for d in day_dirs]


@pytest.mark.structure
def test_day_folders_are_numbered_01_to_21_in_order(day_dirs):
    assert [d.name[:2] for d in day_dirs] == [f"{n:02d}" for n in range(1, 22)]


@pytest.mark.structure
def test_day_folder_names_are_lowercase_snake_case(day_dirs):
    bad = [d.name for d in day_dirs if not DAY_NAME.match(d.name)]
    assert not bad, f"badly named day folders: {bad}"


@pytest.mark.structure
def test_every_day_has_a_readme(day_dirs):
    missing = [d.name for d in day_dirs if not (d / "README.md").is_file()]
    assert not missing, f"day folders without README.md: {missing}"


@pytest.mark.structure
def test_every_readme_has_a_day_heading(day_dirs):
    """Each README must open with `# Day NN — Title` matching its folder."""
    wrong = []
    for d in day_dirs:
        first_line = (d / "README.md").read_text(encoding="utf-8").splitlines()[0]
        if not first_line.startswith(f"# Day {d.name[:2]} — "):
            wrong.append((d.name, first_line[:60]))
    assert not wrong, f"READMEs with a mismatched heading: {wrong}"


@pytest.mark.structure
def test_repo_scaffolding_exists(repo_root):
    for name in ("requirements.txt", "pytest.ini", ".gitignore", "README.md"):
        assert (repo_root / name).is_file(), f"missing {name}"
