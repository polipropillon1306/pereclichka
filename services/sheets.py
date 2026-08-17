import os
import logging
import asyncio
from typing import List, Tuple, Optional
import gspread
import google.auth
from google.oauth2.service_account import Credentials
from config import GOOGLE_SERVICE_ACCOUNT_FILE

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

_sync_lock = asyncio.Lock()

def get_gspread_client():
    try:
        if os.path.exists(GOOGLE_SERVICE_ACCOUNT_FILE):
            creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        else:
            creds, _ = google.auth.default(scopes=SCOPES)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        logger.error(f"Ошибка авторизации Google API: {e}")
        return None

def normalize_key(s: str) -> str:
    """Нормализует имя/username (убирает @, пробелы и приводит к нижнему регистру)"""
    return s.strip().lstrip('@').lower()

def sync_rollcall_to_sheet(sheet_url: str, target_date: str, votes: List[Tuple]) -> Optional[str]:
    """
    votes: list of (user_id, username, full_name, status, checked_in)
    Синхронизирует результаты переклички в Google Таблицу пакетным обновлением (Batch Update).
    Возвращает None в случае успеха или текст ошибки при сбое.
    """
    if not sheet_url:
        return None
    
    client = get_gspread_client()
    if not client:
        return "Не удалось авторизоваться в Google API (проверьте файл ключа)"

    try:
        spreadsheet = client.open_by_url(sheet_url)
        worksheet = spreadsheet.sheet1

        # 1. Считываем всю таблицу за 1 сетевой запрос
        all_values = worksheet.get_all_values()

        if not all_values:
            headers = ["Имя участника", "Telegram"]
            all_values = [headers]
        else:
            headers = list(all_values[0])

        # 2. Добавляем колонку с датой, если ее еще нет
        if target_date not in headers:
            headers.append(target_date)
            all_values[0] = headers

        date_col_idx = headers.index(target_date)
        num_cols = len(headers)

        # 3. Индексируем существующих участников
        user_row_map = {}
        for idx, row in enumerate(all_values[1:], start=1):
            name = row[0] if len(row) > 0 else ""
            username = row[1] if len(row) > 1 else ""
            if username:
                user_row_map[normalize_key(username)] = idx
            if name:
                user_row_map[normalize_key(name)] = idx

        # 4. Формируем маппинг активных голосов
        active_votes_map = {}
        for user_id, username, full_name, status, checked_in in votes:
            status_text = status
            if status == '+' and checked_in:
                status_text = "+ (Пришел)"
            elif status == '+' and not checked_in:
                status_text = "+ (Ожидается)"
            elif status == '-':
                status_text = "- (Не будет)"

            cell_value = f"'{status_text}" if status_text.startswith(('+', '-', '=')) else status_text
            
            key = normalize_key(username) if username else normalize_key(full_name)
            active_votes_map[key] = (cell_value, full_name, username)

        # 5. Обновляем строки существующих пользователей
        matched_keys = set()
        for idx, row in enumerate(all_values[1:], start=1):
            while len(row) < num_cols:
                row.append("")

            name = row[0] if len(row) > 0 else ""
            username = row[1] if len(row) > 1 else ""
            u_key = normalize_key(username) if username else None
            n_key = normalize_key(name) if name else None

            found_key = None
            if u_key and u_key in active_votes_map:
                found_key = u_key
            elif n_key and n_key in active_votes_map:
                found_key = n_key

            if found_key:
                matched_keys.add(found_key)
                row[date_col_idx] = active_votes_map[found_key][0]
            else:
                row[date_col_idx] = "Не отметился"

            all_values[idx] = row

        # 6. Добавляем новых участников, которых еще не было в таблице
        for key, (cell_value, full_name, username) in active_votes_map.items():
            if key not in matched_keys:
                new_row = [full_name, f"@{username}" if username else ""]
                while len(new_row) < num_cols:
                    new_row.append("Не отметился")
                new_row[date_col_idx] = cell_value
                all_values.append(new_row)
                matched_keys.add(key)

        # 7. Записываем ВСЮ таблицу за 1 пакетный запрос (быстро и без исчерпания квот)
        worksheet.update(all_values, "A1")
        logger.info(f"Успешно синхронизировано в Google Sheets (batch update) для даты {target_date}")
        return None
    except Exception as e:
        err_msg = f"Ошибка записи в Google Sheets: {e}"
        logger.error(err_msg)
        return err_msg

async def async_sync_rollcall_to_sheet(sheet_url: str, target_date: str, votes: List[Tuple], bot=None, chat_id: int = None):
    async with _sync_lock:
        error = await asyncio.to_thread(sync_rollcall_to_sheet, sheet_url, target_date, votes)
        if error and bot and chat_id:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "⚠️ <b>Внимание: Ошибка синхронизации с Google Таблицей!</b>\n\n"
                        "Пожалуйста, убедитесь, что таблица доступна и сервисный аккаунт добавлен в нее с правами Редактора."
                    ),
                    parse_mode="HTML"
                )
            except Exception:
                pass
