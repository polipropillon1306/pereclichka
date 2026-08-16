from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_rollcall_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👍 Буду (+)", callback_data="vote_+"),
                InlineKeyboardButton(text="👎 Не буду (-)", callback_data="vote_-")
            ]
        ]
    )
    return keyboard
