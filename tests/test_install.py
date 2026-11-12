"""scripts/install.sh のテスト（bash が無い環境ではスキップ）。"""
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install.sh"
SKILLS = ["pdf-to-md-ja", "url-to-md"]

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash が無い")


def run(*args, stdin=subprocess.DEVNULL):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True,
                          encoding="utf-8", stdin=stdin)


def test_dry_run_writes_nothing(tmp_path):
    dest = tmp_path / "skills"
    r = run("--dir", str(dest), "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "DRY-RUN" in r.stdout
    assert not dest.exists()


def test_install_and_uninstall(tmp_path):
    dest = tmp_path / "skills"
    assert run("--dir", str(dest), "-y").returncode == 0
    for s in SKILLS:
        assert (dest / s / "SKILL.md").is_file()
    assert run("--dir", str(dest), "--uninstall", "-y").returncode == 0
    for s in SKILLS:
        assert not (dest / s).exists()


def test_reinstall_removes_stale_files(tmp_path):
    dest = tmp_path / "skills"
    run("--dir", str(dest), "-y")
    stale = dest / "url-to-md" / "stale.txt"
    stale.write_text("x")
    run("--dir", str(dest), "-y")
    assert not stale.exists()


def test_non_interactive_without_yes_skips(tmp_path):
    dest = tmp_path / "skills"
    r = run("--dir", str(dest))
    assert r.returncode == 0
    assert not dest.exists()


def test_short_target_alias_and_unknown_target():
    assert run("-t", "copilot", "--dry-run").returncode == 0
    r = run("--target", "nonexistent", "--dry-run")
    assert r.returncode != 0


def test_list_targets():
    r = run("--list-targets")
    assert r.returncode == 0
    for t in ("claude", "copilot", "agents", "antigravity"):
        assert t in r.stdout


@pytest.mark.skipif(shutil.which("zip") is None, reason="zip が無い")
def test_zip_has_skill_md_at_top():
    before = set((ROOT / "dist").glob("*.zip")) if (ROOT / "dist").exists() else set()
    r = run("--zip")
    assert r.returncode == 0, r.stderr
    made = set((ROOT / "dist").glob("*.zip")) - before
    try:
        assert len(made) == 2
        for z in made:
            name = z.name.rsplit("-", 2)[0]
            assert name in SKILLS
            assert f"{name}/SKILL.md" in zipfile.ZipFile(z).namelist()
    finally:
        for z in made:
            z.unlink()


def test_gitattributes_pins_sh_to_lf():
    assert "*.sh text eol=lf" in (ROOT / ".gitattributes").read_text()
    assert b"\r" not in SCRIPT.read_bytes()
