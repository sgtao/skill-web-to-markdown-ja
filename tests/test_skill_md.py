"""P5: SKILL.md・workflows/ の記述が、スクリプトの --help と食い違わないことの照合。"""
import glob
import os
import re
import subprocess
import sys

import pytest

import _common

SCRIPTS = ["fetch_convert.py", "html_to_md.py", "youtube_to_md.py",
           "add_frontmatter.py", "office_extract.py"]


@pytest.fixture(scope="module")
def skill_dir(scripts_dir):
    return os.path.dirname(scripts_dir)


def _skill_texts(skill_dir):
    paths = [os.path.join(skill_dir, "SKILL.md")]
    paths += sorted(glob.glob(os.path.join(skill_dir, "workflows", "*.md")))
    return {p: open(p, encoding="utf-8").read() for p in paths}


@pytest.fixture(scope="module")
def help_text(scripts_dir):
    out = []
    for s in SCRIPTS:
        r = subprocess.run([sys.executable, os.path.join(scripts_dir, s), "--help"],
                           capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        assert r.returncode == 0, s
        out.append(r.stdout.decode("utf-8"))
    return "\n".join(out)


def test_every_option_in_skill_md_exists_in_help(skill_dir, help_text):
    missing = []
    for path, text in _skill_texts(skill_dir).items():
        for opt in sorted(set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", text))):
            if opt not in help_text:
                missing.append((os.path.basename(path), opt))
    assert not missing, missing


def test_skill_md_is_not_an_option_list(skill_dir):
    """オプションの一覧は --help が正。入口の SKILL.md は短く保つ。"""
    text = open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8").read()
    assert len(text.splitlines()) <= 130


def test_every_reason_code_has_a_row(skill_dir):
    text = open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8").read()
    for code in list(_common.REASON_MEANINGS) + ["UNEXPECTED"]:
        assert f"`{code}`" in text, code


def test_conversion_values_match_the_script(skill_dir):
    from add_frontmatter import CONVERSIONS
    text = open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8").read()
    for v in CONVERSIONS:
        assert f"`{v}`" in text, v


def test_workflow_files_referenced_exist(skill_dir):
    for path, text in _skill_texts(skill_dir).items():
        for ref in re.findall(r"workflows/[\w-]+\.md", text):
            assert os.path.exists(os.path.join(skill_dir, ref)), (path, ref)


def test_scripts_referenced_exist(skill_dir):
    for path, text in _skill_texts(skill_dir).items():
        for ref in re.findall(r"scripts/([\w]+\.py)", text):
            assert os.path.exists(os.path.join(skill_dir, "scripts", ref)), (path, ref)


def test_no_hardcoded_python3_or_web_fetch(skill_dir):
    for path, text in _skill_texts(skill_dir).items():
        assert "python3 scripts/" not in text, path
        assert "web_fetch" not in text, path
        assert "--pdf-passthrough" not in text, path
