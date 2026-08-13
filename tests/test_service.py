from dataclasses import dataclass, field

import pytest

from herald.config import Config, FilePolicy, PlatformConfig, ProjectConfig, RouteConfig
from herald.domain import Attachment, Destination, FormattedText, Message
from herald.service import Herald, render_update


@dataclass
class FakeMessenger:
    sent: list[tuple[Destination, FormattedText]] = field(default_factory=list)
    files: list[tuple[Destination, Attachment, FormattedText]] = field(
        default_factory=list
    )

    def send(self, destination: Destination, content: FormattedText) -> int:
        self.sent.append((destination, content))
        return 123

    def send_file(
        self,
        destination: Destination,
        attachment: Attachment,
        caption: FormattedText,
    ) -> int:
        self.files.append((destination, attachment, caption))
        return 456


def test_send_uses_project_route_and_renders_metadata() -> None:
    destination = Destination(chat_id="-1001", topic_id=42)
    config = Config(
        platforms={
            "tg": PlatformConfig(type="telegram", token_env="TOKEN", token_file=None)
        },
        routes={"work": RouteConfig(platform="tg", destination=destination)},
        projects={"herald": ProjectConfig(label="Herald", route="work")},
    )
    adapter = FakeMessenger()
    service = Herald(config, {"tg": adapter})

    receipt = service.send(
        Message(
            text="Прототип готов",
            agent="Codex",
            model="GPT",
            project="herald",
            subject="MCP",
            reference="ctx:herald#d001",
        )
    )

    assert receipt.message_id == 123
    assert adapter.sent == [
        (
            destination,
            FormattedText(
                "Прототип готов\n\nctx:herald#d001\n\n— Codex · GPT · Herald · MCP",
                "plain",
            ),
        )
    ]


def test_html_preserves_content_and_escapes_generated_metadata() -> None:
    destination = Destination(chat_id="1", topic_id=5)
    config = Config(
        platforms={
            "tg": PlatformConfig(type="telegram", token_env="TOKEN", token_file=None)
        },
        routes={"flowers": RouteConfig(platform="tg", destination=destination)},
        projects={"flowers": ProjectConfig(label="172 <Цветы>", route="flowers")},
    )
    adapter = FakeMessenger()

    Herald(config, {"tg": adapter}).send(
        Message(
            text="<b>Готово</b>",
            agent="Codex",
            model="GPT",
            project="flowers",
            subject="цены & остатки",
            format="html",
        )
    )

    assert adapter.sent[0][1] == FormattedText(
        "<b>Готово</b>\n\n<i>— Codex · GPT · 172 &lt;Цветы&gt; · цены &amp; остатки</i>",
        "html",
    )


def test_rejects_escaped_html_tags() -> None:
    destination = Destination(chat_id="1", topic_id=2)
    config = Config(
        platforms={
            "tg": PlatformConfig(type="telegram", token_env="TOKEN", token_file=None)
        },
        routes={"aleon": RouteConfig(platform="tg", destination=destination)},
        projects={"aleon": ProjectConfig(label="Алеон", route="aleon")},
    )

    with pytest.raises(ValueError, match="raw Telegram HTML"):
        Herald(config, {"tg": FakeMessenger()}).send(
            Message(
                text="&lt;b&gt;Готово&lt;/b&gt;",
                agent="Claude",
                model="Opus",
                project="aleon",
                subject="отчёт",
                format="html",
            )
        )


def test_rejects_missing_provenance() -> None:
    config = Config(
        platforms={
            "tg": PlatformConfig(type="telegram", token_env="TOKEN", token_file=None)
        },
        routes={
            "work": RouteConfig(platform="tg", destination=Destination(chat_id="1"))
        },
        projects={"herald": ProjectConfig(label="Herald", route="work")},
    )
    service = Herald(config, {"tg": FakeMessenger()})

    with pytest.raises(ValueError, match="agent"):
        service.send(Message("text", "", "GPT", "herald", "MCP"))


def test_render_update_is_structured_and_escapes_values() -> None:
    rendered = render_update(
        summary="Цены <готовы>",
        completed=["Загружено 126 строк"],
        blockers=["Нужно правило доставки"],
        decisions_needed=[],
        client_questions=["Какова цена ниже порога?"],
        next_steps=["Обновить оферту"],
        preset="brief",
    )

    assert rendered == (
        "<b>Итог</b>\nЦены &lt;готовы&gt;\n\n"
        "<b>Сделано</b>\n1. Загружено 126 строк\n\n"
        "<b>Стоперы</b>\n1. Нужно правило доставки\n\n"
        "<b>Вопросы клиенту</b>\n1. Какова цена ниже порога?\n\n"
        "<b>Дальше</b>\n1. Обновить оферту"
    )


def test_render_update_rejects_prose_in_brief_item() -> None:
    with pytest.raises(ValueError, match="one line"):
        render_update(
            summary="Статус",
            completed=["Первая строка\nВторая строка"],
            blockers=[],
            decisions_needed=[],
            client_questions=[],
            next_steps=[],
            preset="brief",
        )


def test_send_file_requires_allowed_root_and_adds_metadata(tmp_path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    attachment = allowed / "report.pdf"
    attachment.write_bytes(b"pdf")
    destination = Destination(chat_id="1", topic_id=2)
    config = Config(
        platforms={
            "tg": PlatformConfig(type="telegram", token_env="TOKEN", token_file=None)
        },
        routes={"work": RouteConfig(platform="tg", destination=destination)},
        projects={"herald": ProjectConfig(label="Herald", route="work")},
        files=FilePolicy(allowed_roots=(allowed.resolve(),)),
    )
    adapter = FakeMessenger()

    receipt = Herald(config, {"tg": adapter}).send_file(
        path=attachment,
        kind="auto",
        caption=Message(
            "Отчёт",
            "Codex",
            "GPT",
            "herald",
            "Файл",
            format="html",
        ),
    )

    assert receipt.message_id == 456
    assert adapter.files[0][1] == Attachment(attachment.resolve(), "auto")
    assert "<i>— Codex · GPT · Herald · Файл</i>" in adapter.files[0][2].text


def test_send_file_rejects_path_outside_allowed_root(tmp_path) -> None:
    attachment = tmp_path / "secret.txt"
    attachment.write_text("secret", encoding="utf-8")
    config = Config(
        platforms={
            "tg": PlatformConfig(type="telegram", token_env="TOKEN", token_file=None)
        },
        routes={
            "work": RouteConfig(platform="tg", destination=Destination(chat_id="1"))
        },
        projects={"herald": ProjectConfig(label="Herald", route="work")},
    )

    with pytest.raises(ValueError, match="allowed_roots"):
        Herald(config, {"tg": FakeMessenger()}).send_file(
            path=attachment,
            kind="document",
            caption=Message("Файл", "Codex", "GPT", "herald", "Файл"),
        )
