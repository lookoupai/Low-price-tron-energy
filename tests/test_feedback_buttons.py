import asyncio
import os
import time
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
from telegram import Bot, Update
from telegram.request import BaseRequest

import db
from blacklist_manager import BlacklistManager
from feedback_manager import FeedbackManager
from feedback_ui import public_keyboard
from telegram_bot import TronEnergyBot
from whitelist_manager import WhitelistManager


PAYMENT = "T" + "A" * 33
PROVIDER = "T" + "B" * 33
TEXT = f"🔹 【收款地址】: {PAYMENT}\n🔹 【能量提供方】: {PROVIDER}"


class TelegramRequests(BaseRequest):
    """Capture PTB's serialized Bot API requests without contacting Telegram."""

    def __init__(self):
        self.calls = []
        self.fail_receipt = False

    @property
    def read_timeout(self):
        return 2

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        payload = request_data.parameters
        self.calls.append((endpoint, payload))
        if endpoint == "sendMessage" and self.fail_receipt:
            return 400, b'{"ok":false,"error_code":400,"description":"Bad Request: expired"}'
        return 200, b'{"ok":true,"result":true}'


def card(markup, *, text=TEXT, age_days=0, ephemeral_id=None):
    message = {
        "message_id": 91, "chat": {"id": -1009999, "type": "channel"},
        "date": int(time.time() - age_days * 86400), "text": text, "reply_markup": markup,
    }
    if ephemeral_id is not None:
        message.update(message_id=0, ephemeral_message_id=ephemeral_id)
    return message


async def click(app, person, message, data):
    transport = TelegramRequests()
    bot = Bot("123456:test", request=transport, get_updates_request=transport)
    update = Update.de_json({"update_id": 1, "callback_query": {
        "id": f"query-{person}", "from": {"id": person, "is_bot": False, "first_name": "Reader"},
        "chat_instance": "channel", "message": message, "data": data,
    }}, bot)
    transport.fail_receipt = getattr(app, "fail_receipt", False)
    await app.inline_button_handler(update, SimpleNamespace(bot=bot))
    assert [name for name, _ in transport.calls].count("answerCallbackQuery") == 1
    return transport.calls


@pytest.fixture
async def app(monkeypatch):
    dsn = os.environ.get("TEST_FEEDBACK_DSN")
    if not dsn:
        pytest.skip("Set TEST_FEEDBACK_DSN to run the PostgreSQL integration cases")
    schema = "feedback_test_" + uuid4().hex
    setup = await asyncpg.connect(dsn)
    await setup.execute(f"CREATE SCHEMA {schema}")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=6, server_settings={"search_path": schema})
    try:
        monkeypatch.setattr(db._db_pool, "_pool", pool)
        app = TronEnergyBot.__new__(TronEnergyBot)
        app.feedback_manager = FeedbackManager()
        app.whitelist_manager = WhitelistManager()
        app.blacklist_manager = BlacklistManager()
        await app.feedback_manager.init_database()
        await app.whitelist_manager.init_database()
        await app.blacklist_manager.init_database()
        yield app
    finally:
        await pool.close()
        await setup.execute(f"DROP SCHEMA {schema} CASCADE")
        await setup.close()


def test_public_card_copies_payment_and_uses_short_callbacks():
    markup = public_keyboard(PAYMENT, "p" + "a" * 32).to_dict()["inline_keyboard"]
    assert markup[0] == [{"text": "复制收款地址", "copy_text": {"text": PAYMENT}}]
    assert [b["style"] for b in markup[1]] == ["success", "danger"]
    assert all(len(b["callback_data"].encode()) <= 64 for row in markup[1:] for b in row)


@pytest.mark.parametrize("text,expected", [
    (TEXT, (PAYMENT, PROVIDER)),
    (TEXT.replace(PAYMENT, f"`{PAYMENT}`").replace(PROVIDER, f"`{PROVIDER}`"), (PAYMENT, PROVIDER)),
    (TEXT.replace(PROVIDER, PROVIDER[:-1]), None),
    (TEXT + f"\n【收款地址】: {PROVIDER}", None),
])
def test_legacy_card_recovers_only_unambiguous_labelled_addresses(text, expected):
    assert TronEnergyBot._parse_message_for_addresses(text) == expected


async def test_readers_vote_independently_and_revoke_just_their_own_votes(app):
    keyboard = await app._build_inline_keyboard({"address": PAYMENT, "energy_provider": PROVIDER})
    original = card(keyboard.to_dict())
    data = keyboard.inline_keyboard[1][0].callback_data
    key = data.split(":")[1]
    # A fresh manager can handle messages sent before a process restart.
    app.feedback_manager = FeedbackManager()
    first, second = await asyncio.gather(click(app, 1, original, data), click(app, 2, original, data))
    for person, calls in enumerate((first, second), 1):
        assert [name for name, _ in calls] == ["sendMessage", "answerCallbackQuery"]
        receipt = calls[0][1]
        assert "chat_id" not in receipt and "receiver_user_id" not in receipt["ephemeral_message_parameters"]
        assert receipt["ephemeral_message_parameters"] == {
            "callback_query_id": f"query-{person}", "replace_callback_query_message": True,
        }
        assert receipt["reply_markup"]["inline_keyboard"][0][0]["disabled"] == {}

    assert (await app.whitelist_manager.check_pair(PAYMENT, PROVIDER))["is_provisional"] is False
    panel = card(first[0][1]["reply_markup"], text=first[0][1]["text"], ephemeral_id=17)
    calls = await click(app, 1, panel, f"revoke:{key}")
    assert calls[0][0] == "editEphemeralMessageText"
    assert calls[0][1]["ephemeral_message_id"] == 17 and calls[0][1]["receiver_user_id"] == 1
    assert await app.feedback_manager.get_user_votes(1, PAYMENT, PROVIDER) == []
    assert len(await app.feedback_manager.get_user_votes(2, PAYMENT, PROVIDER)) == 1
    assert (await app.whitelist_manager.check_pair(PAYMENT, PROVIDER))["is_provisional"] is True
    calls = await click(app, 1, panel, f"cancel:{key}")
    assert calls[0][0] == "deleteEphemeralMessage"
    assert (await click(app, 3, original, data))[0][0] == "sendMessage"


async def test_failed_receipt_retry_preserves_deadline_and_flip_resets_it(app):
    keyboard = await app._build_inline_keyboard({"address": PAYMENT, "energy_provider": PROVIDER})
    message = card(keyboard.to_dict())
    yes, no = (b.callback_data for b in keyboard.inline_keyboard[1])
    app.fail_receipt = True
    failed = await click(app, 1, message, yes)
    assert failed[-1][1]["show_alert"] is True
    app.fail_receipt = False
    async with db.db_connection() as conn:
        deadline = await conn.fetchval(
            "UPDATE address_feedback SET created_at=now()-interval '24 hours' RETURNING created_at"
        )
    await click(app, 1, message, yes)
    vote, = await app.feedback_manager.get_user_votes(1, PAYMENT, PROVIDER)
    assert vote["created_at"] == deadline and vote["can_revoke"] is False
    assert not await app.feedback_manager.revoke_votes(1, PAYMENT, PROVIDER)
    await click(app, 1, message, no)
    vote, = await app.feedback_manager.get_user_votes(1, PAYMENT, PROVIDER)
    assert vote["vote_type"] == "fail" and vote["can_revoke"] is True


async def test_old_card_asks_clicker_before_writing_then_edits_their_panel(app):
    legacy = "only_pay_wl:deadbeef00"
    original = card({"inline_keyboard": [[{"text": "加白", "callback_data": legacy}]]}, age_days=8)
    calls = await click(app, 7, original, legacy)
    panel = calls[0][1]
    assert "7 天" in panel["text"] and await app.feedback_manager.get_user_votes(7, PAYMENT, PROVIDER) == []
    confirm = panel["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    calls = await click(app, 7, card(panel["reply_markup"], text=panel["text"], ephemeral_id=2), confirm)
    assert calls[0][0] == "editEphemeralMessageText"
    vote, = await app.feedback_manager.get_user_votes(7, PAYMENT, PROVIDER)
    assert vote["scope"] == "payment"


async def test_list_write_failure_rolls_back_vote(app, monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(app.whitelist_manager, "add_pair", fail)
    with pytest.raises(RuntimeError):
        await app._apply_feedback_vote(1, PAYMENT, PROVIDER, "pair", "success")
    assert await app.feedback_manager.get_user_votes(1, PAYMENT, PROVIDER) == []
    assert await app.whitelist_manager.check_address(PAYMENT, "payment") is None
