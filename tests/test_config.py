import pytest

from aiseed_notify import ConfigError, load
from aiseed_notify.config import read_env_file, resolve

MINIMAL = """
name: t
notify:
  discord: {webhook: "${TEST_HOOK}"}
"""


def _write(tmp_path, text, name="t.yaml"):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_env_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_HOOK", "https://example.invalid/hook")
    cfg = load(_write(tmp_path, MINIMAL))
    assert cfg["notify"]["discord"]["webhook"] == "https://example.invalid/hook"


def test_unset_var_becomes_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_HOOK", raising=False)
    cfg = load(_write(tmp_path, MINIMAL))
    assert cfg["notify"]["discord"]["webhook"] == ""


def test_missing_name_raises(tmp_path):
    with pytest.raises(ConfigError, match="missing required key 'name'"):
        load(_write(tmp_path, "description: no name here\n"))


def test_not_a_mapping_raises(tmp_path):
    with pytest.raises(ConfigError, match="expected a mapping"):
        load(_write(tmp_path, "- just\n- a\n- list\n"))


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="no such config"):
        load(tmp_path / "nope.yaml")


def test_env_file_is_loaded_before_expansion(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_HOOK", raising=False)
    (tmp_path / "env").write_text("# comment\nTEST_HOOK=https://from-file.invalid\n")
    cfg = load(_write(tmp_path, MINIMAL + "env_file: env\n"))
    assert cfg["notify"]["discord"]["webhook"] == "https://from-file.invalid"


def test_real_environment_wins_over_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_HOOK", "https://from-env.invalid")
    (tmp_path / "env").write_text("TEST_HOOK=https://from-file.invalid\n")
    cfg = load(_write(tmp_path, MINIMAL + "env_file: env\n"))
    assert cfg["notify"]["discord"]["webhook"] == "https://from-env.invalid"


def test_read_env_file_ignores_comments_and_strips_quotes(tmp_path):
    (tmp_path / "e").write_text("# c\n\nA=1\nB='two'\nC=\"three\"\nnot_a_pair\n")
    assert read_env_file(tmp_path / "e") == {"A": "1", "B": "two", "C": "three"}


def test_resolve_by_name_under_config_dir(tmp_path, monkeypatch):
    (tmp_path / "job.yaml").write_text("x")
    monkeypatch.setenv("NOTIFY_CONFIG_DIR", str(tmp_path))
    assert resolve("job") == tmp_path / "job.yaml"
