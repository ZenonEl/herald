from pathlib import Path

import pytest

from herald.config import (
    Config,
    PlatformConfig,
    ProjectConfig,
    RouteConfig,
    WatchConfig,
    WatchProfile,
    WatchSource,
)
from herald.domain import Destination
from herald.inbox import Inbox
from herald.watch import WatchInput, WatchStore


def config() -> Config:
    return Config(
        platforms={"telegram": PlatformConfig("telegram", "TOKEN", None)},
        routes={
            "alpha": RouteConfig("telegram", Destination("-100", 132)),
            "beta": RouteConfig("telegram", Destination("-100", 2)),
        },
        projects={
            "alpha": ProjectConfig("Project A", "alpha"),
            "beta": ProjectConfig("Project B", "beta"),
        },
        watch=WatchConfig(
            enabled=True,
            sources={"owner": WatchSource(7, 7)},
            profiles={
                "alpha": WatchProfile(("101", "alpha"), "owner", "alpha"),
                "beta": WatchProfile(("beta",), "owner", "beta"),
            },
        ),
    )


@pytest.fixture()
def watch(tmp_path: Path) -> WatchStore:
    inbox = Inbox(tmp_path / "inbox.db", tmp_path / "files")
    inbox.prepare()
    store = WatchStore(inbox)
    store.prepare()
    return store


def incoming(message_id: int, text: str, **changes) -> WatchInput:
    data = {
        "chat_id": 7,
        "message_id": message_id,
        "user_id": 7,
        "chat_type": "private",
        "text": text,
        "date": "2026-09-01T12:00:00+00:00",
    }
    data.update(changes)
    return WatchInput(**data)


def test_observed_activity_and_monitor_are_not_fake_polls(watch):
    duty = watch.start(
        config(),
        profiles=["alpha"],
        primary_profile=None,
        agent="Test",
        model="Test",
        session_name="test",
    )["duty_id"]
    assert watch.status(duty)["observed_state"] == "registered"
    watch.monitor_probe(duty)
    state = watch.status(duty)
    assert state["monitor_alive"]
    assert state["last_poll_at"] is None
    assert not state["polling_recently"]
    watch.activity(duty, "waiting_user")
    assert watch.status(duty)["last_poll_at"] is None
    watch.wait(duty, timeout=0)
    assert watch.status(duty)["observed_state"] == "waiting_user"
    assert watch.status(duty)["polling_recently"]
    watch.ingest(config().watch, incoming(999, "#alpha Hello"))
    assert len(watch.monitor_probe(duty)["pending"]) == 1
    delivery = watch.wait(duty, timeout=0)
    assert watch.status(duty)["observed_state"] == "processing"
    assert watch.monitor_probe(duty)["pending"] == []
    watch.ack(duty, delivery["delivery_id"], success=True)
    assert watch.status(duty)["observed_state"] == "idle"
    with watch.inbox.connect() as connection:
        connection.execute(
            "UPDATE watch_duties SET activity_at='2000-01-01T00:00:00+00:00', last_poll_at='2000-01-01T00:00:00+00:00', monitor_at='2000-01-01T00:00:00+00:00' WHERE duty_id=?",
            (duty,),
        )
        connection.commit()
    state = watch.status(duty)
    assert state["observed_state"] == "unknown"
    assert not state["polling_recently"] and not state["monitor_alive"]
    watch.prepare()
    assert watch.status(duty)["deliveries"] == {"done": 1}


def test_monitor_does_not_claim_other_duty_or_expose_text(watch):
    cfg = config()
    one = watch.start(
        cfg,
        profiles=["alpha"],
        primary_profile=None,
        agent="Test",
        model="Test",
        session_name="one",
    )["duty_id"]
    two = watch.start(
        cfg,
        profiles=["beta"],
        primary_profile=None,
        agent="Test",
        model="Test",
        session_name="two",
    )["duty_id"]
    watch.ingest(cfg.watch, incoming(998, "#alpha Private command"))
    assert watch.monitor_probe(two)["pending"] == []
    event = watch.monitor_probe(one)
    assert set(event) == {"duty_id", "pending"}
    assert len(event["pending"]) == 1
    assert watch.status(one)["deliveries"] == {"pending": 1}


def test_companion_cli_once_keeps_work_pending(watch, monkeypatch, capsys):
    import json
    from herald import watch_monitor

    cfg = config()
    duty = watch.start(
        cfg,
        profiles=["alpha"],
        primary_profile=None,
        agent="Test",
        model="Test",
        session_name="test",
    )["duty_id"]
    watch.ingest(cfg.watch, incoming(997, "#alpha Hello"))
    monkeypatch.setattr(watch_monitor, "load_config", lambda path: cfg)
    monkeypatch.setattr(watch_monitor, "Inbox", lambda *args: watch.inbox)
    monkeypatch.setattr("sys.argv", ["herald-watch-monitor", duty, "--once"])
    watch_monitor.main()
    event = json.loads(capsys.readouterr().out)
    assert len(event["pending"]) == 1
    assert watch.status(duty)["last_poll_at"] is None
    assert watch.status(duty)["monitor_alive"]


def test_wait_returns_only_the_registered_duties_messages(watch: WatchStore) -> None:
    cfg = config()
    alpha = watch.start(
        cfg,
        profiles=["alpha"],
        primary_profile=None,
        agent="Codex",
        model="GPT",
        session_name="alpha",
    )
    beta = watch.start(
        cfg,
        profiles=["beta"],
        primary_profile=None,
        agent="Claude",
        model="Opus",
        session_name="beta",
    )
    assert watch.ingest(cfg.watch, incoming(1, "#alpha Проверь логи")) == 1

    assert watch.wait(beta["duty_id"], timeout=0) is None
    delivery = watch.wait(alpha["duty_id"], timeout=0)
    assert delivery["text"] == "Проверь логи"
    assert delivery["profile"] == "alpha"


def test_all_is_fanned_out_and_not_a_shared_queue(watch: WatchStore) -> None:
    cfg = config()
    first = watch.start(
        cfg,
        profiles=["alpha"],
        primary_profile=None,
        agent="Codex",
        model="GPT",
        session_name="one",
    )
    second = watch.start(
        cfg,
        profiles=["beta"],
        primary_profile=None,
        agent="Claude",
        model="Opus",
        session_name="two",
    )
    assert watch.ingest(cfg.watch, incoming(2, "#all Статус")) == 2

    assert watch.wait(first["duty_id"], timeout=0)["text"] == "Статус"
    assert watch.wait(second["duty_id"], timeout=0)["text"] == "Статус"


def test_redelivery_does_not_add_new_broadcast_recipients(watch: WatchStore) -> None:
    cfg = config()
    first = watch.start(
        cfg,
        profiles=["alpha"],
        primary_profile=None,
        agent="Codex",
        model="GPT",
        session_name="one",
    )
    message = incoming(20, "#all Статус")
    assert watch.ingest(cfg.watch, message) == 1
    second = watch.start(
        cfg,
        profiles=["beta"],
        primary_profile=None,
        agent="Claude",
        model="Opus",
        session_name="two",
    )

    assert watch.ingest(cfg.watch, message) == 0
    assert watch.wait(first["duty_id"], timeout=0) is not None
    assert watch.wait(second["duty_id"], timeout=0) is None


def test_unaddressed_is_separate_and_all_inspection_is_gated(
    watch: WatchStore,
) -> None:
    cfg = config()
    duty = watch.start(
        cfg,
        profiles=["alpha"],
        primary_profile=None,
        agent="Codex",
        model="GPT",
        session_name="alpha",
    )
    watch.ingest(cfg.watch, incoming(3, "сообщение без адреса"))

    assert watch.wait(duty["duty_id"], timeout=0) is None
    assert (
        len(watch.inspect(duty["duty_id"], scope="unaddressed", allow_all=False)) == 1
    )
    with pytest.raises(ValueError, match="allow_inspect_all"):
        watch.inspect(duty["duty_id"], scope="all", allow_all=False)


def test_input_source_and_address_are_validated_before_delivery(
    watch: WatchStore,
) -> None:
    cfg = config()
    duty = watch.start(
        cfg,
        profiles=["alpha"],
        primary_profile=None,
        agent="Codex",
        model="GPT",
        session_name="alpha",
    )
    assert watch.ingest(cfg.watch, incoming(4, "#alpha secret", user_id=99)) == 0
    watch.ingest(cfg.watch, incoming(5, "#alpha #beta ambiguous"))
    assert watch.wait(duty["duty_id"], timeout=0) is None


def test_inactive_profile_delivery_is_assigned_when_duty_starts(
    watch: WatchStore,
) -> None:
    cfg = config()
    watch.ingest(cfg.watch, incoming(6, "#alpha Позже"))
    duty = watch.start(
        cfg,
        profiles=["alpha"],
        primary_profile=None,
        agent="Codex",
        model="GPT",
        session_name="alpha",
    )
    assert watch.wait(duty["duty_id"], timeout=0)["text"] == "Позже"


def test_delivery_can_only_be_acknowledged_by_its_owner(watch: WatchStore) -> None:
    cfg = config()
    alpha = watch.start(
        cfg,
        profiles=["alpha"],
        primary_profile=None,
        agent="Codex",
        model="GPT",
        session_name="alpha",
    )
    beta = watch.start(
        cfg,
        profiles=["beta"],
        primary_profile=None,
        agent="Claude",
        model="Opus",
        session_name="beta",
    )
    watch.ingest(cfg.watch, incoming(7, "#alpha Готово?"))
    delivery = watch.wait(alpha["duty_id"], timeout=0)

    with pytest.raises(ValueError, match="not claimed"):
        watch.ack(beta["duty_id"], delivery["delivery_id"], success=True)
    assert (
        watch.ack(alpha["duty_id"], delivery["delivery_id"], success=True)["state"]
        == "done"
    )


def test_start_validates_primary_and_conflicts_atomically(watch: WatchStore) -> None:
    cfg = config()
    with pytest.raises(ValueError, match="primary_profile"):
        watch.start(
            cfg,
            profiles=["alpha", "beta"],
            primary_profile=None,
            agent="Codex",
            model="GPT",
            session_name="both",
        )
    watch.start(
        cfg,
        profiles=["alpha"],
        primary_profile=None,
        agent="Codex",
        model="GPT",
        session_name="first",
    )
    with pytest.raises(ValueError, match="already active"):
        watch.start(
            cfg,
            profiles=["alpha"],
            primary_profile=None,
            agent="Claude",
            model="Opus",
            session_name="second",
        )


def test_source_boundary_blocks_another_owners_profile(watch: WatchStore) -> None:
    base = config()
    cfg = Config(
        platforms=base.platforms,
        routes=base.routes,
        projects=base.projects,
        watch=WatchConfig(
            enabled=True,
            sources={
                "owner": WatchSource(7, 7),
                "other_owner": WatchSource(8, 8),
            },
            profiles={
                "alpha": base.watch.profiles["alpha"],
                "beta": WatchProfile(("beta",), "other_owner", "beta"),
            },
        ),
    )
    duty = watch.start(
        cfg,
        profiles=["alpha"],
        primary_profile=None,
        agent="Codex",
        model="GPT",
        session_name="alpha",
    )

    assert watch.ingest(cfg.watch, incoming(20, "#beta Чужая команда")) == 1
    assert watch.wait(duty["duty_id"], timeout=0) is None
    assert (
        watch.inspect(duty["duty_id"], scope="unaddressed", allow_all=False)[0]["text"]
        == "#beta Чужая команда"
    )

    with pytest.raises(ValueError, match="different sources"):
        watch.start(
            cfg,
            profiles=["alpha", "beta"],
            primary_profile="alpha",
            agent="Codex",
            model="GPT",
            session_name="mixed",
        )

    other = watch.start(
        cfg,
        profiles=["beta"],
        primary_profile=None,
        agent="Claude",
        model="Opus",
        session_name="beta",
    )
    assert watch.ingest(cfg.watch, incoming(21, "#all Проверка")) == 1
    assert watch.wait(duty["duty_id"], timeout=0)["text"] == "Проверка"
    assert watch.wait(other["duty_id"], timeout=0) is None
