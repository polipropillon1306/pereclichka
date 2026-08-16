from config import ADMIN_IDS, ALLOWED_CHAT_IDS

def is_admin(user_id: int) -> bool:
    """
    Проверяет, входит ли ID пользователя в список разрешенных администраторов
    """
    return user_id in ADMIN_IDS

def is_allowed_chat(chat_id: int) -> bool:
    """
    Проверяет, разрешена ли работа в данном чате
    """
    if chat_id > 0:
        # Личные сообщения разрешены только для админов
        return chat_id in ADMIN_IDS
    return chat_id in ALLOWED_CHAT_IDS
