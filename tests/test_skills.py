import pytest
from nerdvana_cli.core.skills import Skill, SkillLoader

def test_parse_skill_file(tmp_path):
    f = tmp_path / "review.md"
    f.write_text("---\nname: code-review\ndescription: Review code\ntrigger: /review\n---\n\n# Code Review\nCheck security.\n")
    skill = Skill.from_file(f)
    assert skill.name == "code-review"
    assert skill.trigger == "/review"
    assert "Code Review" in skill.body

def test_auto_trigger_from_name(tmp_path):
    f = tmp_path / "debug.md"
    f.write_text("---\nname: debug\ndescription: Debug\n---\n\nBody\n")
    skill = Skill.from_file(f)
    assert skill.trigger == "/debug"

def test_loader_discovers(tmp_path):
    d = tmp_path / ".nerdvana" / "skills"
    d.mkdir(parents=True)
    (d / "a.md").write_text("---\nname: a\ndescription: A\n---\n\nA body\n")
    (d / "b.md").write_text("---\nname: b\ndescription: B\n---\n\nB body\n")
    (d / "skip.txt").write_text("not a skill")
    loader = SkillLoader(project_dir=str(tmp_path), global_dir=str(tmp_path / "empty"))
    skills = loader.load_all()
    names = {s.name for s in skills}
    assert "a" in names
    assert "b" in names
    assert len(skills) >= 2

def test_project_overrides_global(tmp_path):
    gd = tmp_path / "global"
    gd.mkdir()
    (gd / "r.md").write_text("---\nname: review\ndescription: Global\n---\n\nGlobal\n")
    pd = tmp_path / "project" / ".nerdvana" / "skills"
    pd.mkdir(parents=True)
    (pd / "r.md").write_text("---\nname: review\ndescription: Project\n---\n\nProject\n")
    loader = SkillLoader(project_dir=str(tmp_path / "project"), global_dir=str(gd))
    skills = loader.load_all()
    review = next(s for s in skills if s.name == "review")
    assert "Project" in review.body

def test_get_by_trigger(tmp_path):
    d = tmp_path / ".nerdvana" / "skills"
    d.mkdir(parents=True)
    (d / "r.md").write_text("---\nname: review\ndescription: R\ntrigger: /review\n---\n\nBody\n")
    loader = SkillLoader(project_dir=str(tmp_path), global_dir=str(tmp_path / "empty"))
    loader.load_all()
    assert loader.get_by_trigger("/review") is not None
    assert loader.get_by_trigger("/nope") is None

def test_malformed_ignored(tmp_path):
    d = tmp_path / ".nerdvana" / "skills"
    d.mkdir(parents=True)
    (d / "bad.md").write_text("no frontmatter")
    (d / "good.md").write_text("---\nname: good\ndescription: G\n---\n\nBody\n")
    loader = SkillLoader(project_dir=str(tmp_path), global_dir=str(tmp_path / "empty"))
    skills = loader.load_all()
    names = {s.name for s in skills}
    assert "good" in names
    assert "bad" not in names
