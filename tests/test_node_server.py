"""Host-level tools, and the allowlist that bounds what the agent may look at."""

import json

import pytest

from aiseed_notify.mcp import node_server


@pytest.fixture(autouse=True)
def allowlist(monkeypatch, tmp_path):
    monkeypatch.setattr(node_server, "_allowed_paths", [str(tmp_path)])
    monkeypatch.setattr(node_server, "_allowed_units", ["fixture.service"])
    return tmp_path


def test_disk_usage_all_allowlisted(allowlist):
    out = node_server.disk_usage(None)
    assert len(out["mounts"]) == 1
    m = out["mounts"][0]
    assert m["path"] == str(allowlist)
    assert 0 <= m["used_pct"] <= 100
    assert m["total_gb"] > 0


def test_disk_usage_specific_allowlisted_path(allowlist):
    out = node_server.disk_usage(str(allowlist))
    assert out["mounts"][0]["path"] == str(allowlist)


def test_disk_usage_refuses_paths_outside_the_allowlist():
    out = node_server.disk_usage("/etc")
    assert "not allowlisted" in out["error"]
    assert "mounts" not in out


def test_systemd_status_refuses_units_outside_the_allowlist():
    out = node_server.systemd_status("sshd.service")
    assert "not allowlisted" in out["error"]
    assert "units" not in out





def test_load_node_config_sets_the_allowlists(tmp_path):
    p = tmp_path / "n.yaml"
    p.write_text("host: 127.0.0.1\nport: 9999\ndisk_paths: [/tmp]\nsystemd_units: [a.service]\n")
    cfg = node_server.load_node_config(p)
    assert cfg["port"] == 9999
    assert node_server._allowed_paths == ["/tmp"]
    assert node_server._allowed_units == ["a.service"]
