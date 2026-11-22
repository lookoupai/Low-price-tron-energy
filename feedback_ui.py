"""Telegram feedback keyboards and Bot API 10.4 support for PTB 22.8."""
from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from feedback_manager import SCOPE_PAIR, SCOPE_PAYMENT, SCOPE_PROVIDER, VOTE_FAIL, VOTE_SUCCESS


VOTE_ACTIONS = {
    "vote_success": (SCOPE_PAIR, VOTE_SUCCESS),
    "vote_fail": (SCOPE_PAIR, VOTE_FAIL),
    "only_pay_wl": (SCOPE_PAYMENT, VOTE_SUCCESS),
    "only_pay_bl": (SCOPE_PAYMENT, VOTE_FAIL),
    "only_prov_wl": (SCOPE_PROVIDER, VOTE_SUCCESS),
    "only_prov_bl": (SCOPE_PROVIDER, VOTE_FAIL),
}
SCOPE_LABELS = {SCOPE_PAIR: "两者", SCOPE_PAYMENT: "仅收款地址", SCOPE_PROVIDER: "仅提供方"}


def button(text, action, key, style=None):
    return InlineKeyboardButton(text, callback_data=f"{action}:{key}", style=style)


def pair_buttons(key):
    return [button("已到账", "vote_success", key, "success"),
            button("未到账", "vote_fail", key, "danger")]


def public_keyboard(payment, key):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("复制收款地址", copy_text=CopyTextButton(payment))],
        pair_buttons(key),
        [button("我的反馈", "more_ops", key)],
    ])


def personal_panel(key, votes, notice, *, show_options):
    rows = []
    for vote in votes:
        result = "已到账" if vote["vote_type"] == VOTE_SUCCESS else "未到账"
        label = f"已记录 · {SCOPE_LABELS[vote['scope']]} · {result}"
        rows.append([InlineKeyboardButton(label, api_kwargs={"disabled": {}})])
    if show_options:
        rows.extend([
            pair_buttons(key),
            [button("仅收款地址成功", "only_pay_wl", key), button("仅收款地址有问题", "only_pay_bl", key)],
            [button("仅提供方成功", "only_prov_wl", key), button("仅提供方有问题", "only_prov_bl", key)],
        ])
    else:
        rows.append([button("我的反馈", "more_ops", key)])
    if any(vote["can_revoke"] for vote in votes):
        rows.append([button("撤回本人 24 小时内的反馈", "revoke", key)])
    rows.append([button("返回卡片", "cancel", key)])
    return notice, InlineKeyboardMarkup(rows)


def ephemeral_id(query):
    # PTB 22.8 retains newer Bot API fields in api_kwargs.
    return query.message.api_kwargs.get("ephemeral_message_id") if query.message else None


async def show_panel(bot, query, text, markup):
    message_id = ephemeral_id(query)
    if message_id is None:
        # PTB drops None from the wire. Without chat_id this can only be a personal reply.
        parameters = {
            "callback_query_id": query.id, "replace_callback_query_message": True,
        }
        await bot.send_message(
            chat_id=None, text=text, reply_markup=markup,
            api_kwargs={"ephemeral_message_parameters": parameters},
        )
    else:
        await bot.do_api_request("editEphemeralMessageText", {
            "text": text, "reply_markup": markup.to_dict(), "chat_id": query.message.chat_id,
            "receiver_user_id": query.from_user.id, "ephemeral_message_id": message_id,
        })


async def close_panel(bot, query):
    message_id = ephemeral_id(query)
    if message_id is not None:
        await bot.do_api_request("deleteEphemeralMessage", {
            "chat_id": query.message.chat_id, "receiver_user_id": query.from_user.id,
            "ephemeral_message_id": message_id,
        })
