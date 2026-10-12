"""The alarm watch posts a problem once, keeps quiet while it lasts, and posts its recovery once."""

from aiseed_notify import systemd, watch

CAM3 = {"bmonitor:cam3:hung": {"subject": "bmonitor:cam3", "severity": "critical", "message": "cam3 hung"}}


def _run(state, checks, confirm=2):
    """Feed several checks; return what fired and resolved on each."""
    out = []
    for found, quiet in checks:
        fired, resolved = watch.step(state, found, quiet, confirm, "now")
        out.append(([e["alarm"]["message"] for e in fired], [e["alarm"]["message"] for e in resolved]))
    return out


def test_an_alarm_fires_once_after_two_checks_and_stays_silent():
    assert _run({}, [(CAM3, set())] * 4) == [([], []), (["cam3 hung"], []), ([], []), ([], [])]


def test_a_blip_seen_once_never_fires():
    assert _run({}, [(CAM3, set()), ({}, set()), (CAM3, set())]) == [([], []), ([], []), ([], [])]


def test_a_fired_alarm_resolves_once_after_two_clear_checks():
    out = _run({}, [(CAM3, set()), (CAM3, set()), ({}, set()), ({}, set()), ({}, set())])
    assert out[2:] == [([], []), ([], ["cam3 hung"]), ([], [])]


def test_one_clear_check_between_alarms_does_not_resolve():
    out = _run({}, [(CAM3, set()), (CAM3, set()), ({}, set()), (CAM3, set()), ({}, set())])
    assert all(resolved == [] for _, resolved in out)


def test_a_camera_out_of_schedule_keeps_its_alarm_open():
    """Off-schedule says nothing about recovery, so it must not read as resolved."""
    state = {}
    _run(state, [(CAM3, set()), (CAM3, set())])
    out = _run(state, [({}, {"bmonitor:cam3"})] * 5)
    assert out == [([], [])] * 5 and "bmonitor:cam3:hung" in state


def test_an_unreachable_service_holds_every_alarm_it_raised():
    state = {}
    _run(state, [(CAM3, set()), (CAM3, set())])
    down = {"bmonitor:unreachable": {"subject": "bmonitor", "severity": "critical", "message": "down"}}
    out = _run(state, [(down, {"bmonitor:*"}), (down, {"bmonitor:*"})])
    assert out == [([], []), (["down"], [])] and "bmonitor:cam3:hung" in state


def test_the_watch_config_installs_a_watch_unit():
    cfg = {"name": "watch", "description": "Alarm", "path": "/x/watch.yaml", "watch": {},
           "schedule": {"on_calendar": "*:0/5"}}
    assert "-m aiseed_notify watch /x/watch.yaml" in systemd.render(cfg)["notify-watch.service"]
