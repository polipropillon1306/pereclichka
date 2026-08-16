import os
import logging
import asyncio
from typing import List, Tuple
import gspread
from google.oauth2.service_account import Credentials
from config import GOOGLE_SERVICE_ACCOUNT_FILE

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_gspread_client():
    if not os.path.exists(GOOGLE_SERVICE_ACCOUNT_FILE):
        logger.warning(f"Файл ключа Google {GOOGLE_SERVICE_ACCOUNT_FILE} не найден.")
        return None
    try:
        creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        logger.error(f"Ошибка авторизации Google API: {e}")
        return None

def sync_rollcall_to_sheet(sheet_url: str, target_date: str, votes: List[Tuple]):
    """
    votes: list of (user_id, username, full_name, status, checked_in)
    Синхронизирует результаты переклички в Google Таблицу.
    Колонки: Имя участника | Telegram | Дата1 | Дата2 ...
    """
    if not sheet_url:
        return
    
    client = get_gspread_client()
    if not client:
        return

    try:
        spreadsheet = client.open_by_url(sheet_url)
        worksheet = spreadsheet.sheet1  # Берем первый лист

        # Считываем существующие заголовки (строка 1)
        headers = worksheet.row_values(1)
        if not headers:
            headers = ["Имя участника", "Telegram"]
            worksheet.append_row(headers)

        if target_date not in headers:
            headers.append(target_date)
            worksheet.update_cell(1, len(headers), target_date)

        date_col_idx = headers.index(target_date) + 1

        # Считываем всех участников из таблицы (столбец Telegram / Имя)
        existing_users = worksheet.get_all_values()
        user_row_map = {}
        for idx, row in enumerate(existing_users[1:], start=2):
            # idx - номер строки в gspread
            username = row[1] if len(row) > 1 else ""
            name = row[0] if len(row) > 0 else ""
            if username:
                user_row_map[username.lower()] = idx
            elif name:
                user_row_map[name.lower()] = idx

        for user_id, username, full_name, status, checked_in in votes:
            user_identifier = f"@{username}" if username else full_name
            lookup_key = (username or full_name).lower()

            status_text = status
            if status == '+' and checked_in:
                status_text = "+ (Пришел)"
            elif status == '+' and not checked_in:
                status_text = "+ (Ожидается)"

            if lookup_key in user_row_map:
                row_num = user_row_map[lookup_key]
                worksheet.update_cell(row_num, date_col_idx, status_text)
            else:
                # Добавляем нового участника
                new_row = [full_name, f"@{username}" if username else ""]
                # Заполняем пустыми значениями до нужной колонки
                while len(new_row) < date_col_idx - 1:
                    new_row.append("")
                new_row.append(status_text)
                worksheet.append_row(new_row)
                user_row_map[lookup_key] = len(existing_users) + 1
                existing_users.append(new_row)

        logger.info(f"Успешно синхронизировано в Google Sheets для даты {target_date}")
    except Exception as e:
        logger.error(f"Ошибка записи в Google Sheets: {e}")

async def async_sync_rollcall_to_sheet(sheet_url: str, target_date: str, votes: List[Tuple]):
    await asyncio.to_thread(sync_rollcall_to_sheet, sheet_url, target_date, votes)
