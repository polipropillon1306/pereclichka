import os
import logging
import asyncio
from typing import List, Tuple, Optional
import gspread
import google.auth
from google.oauth2.service_account import Credentials
from config import GOOGLE_SERVICE_ACCOUNT_FILE
from bot.utils import get_month_sheet_title

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

def apply_conditional_formatting(spreadsheet, sheet_id: int):
    """Настраивает автоматическую подсветку статусов в Google Таблице"""
    rules = [
        # Зеленый — Пришел
        {
            'addConditionalFormatRule': {
                'rule': {
                    'ranges': [{'sheetId': sheet_id, 'startRowIndex': 1, 'startColumnIndex': 2}],
                    'booleanRule': {
                        'condition': {'type': 'TEXT_CONTAINS', 'values': [{'userEnteredValue': 'Пришел'}]},
                        'format': {
                            'backgroundColor': {'red': 0.85, 'green': 0.92, 'blue': 0.83},
                            'textFormat': {'foregroundColor': {'red': 0.15, 'green': 0.31, 'blue': 0.07}, 'bold': True}
                        }
                    }
                },
                'index': 0
            }
        },
        # Желтый — Ожидается
        {
            'addConditionalFormatRule': {
                'rule': {
                    'ranges': [{'sheetId': sheet_id, 'startRowIndex': 1, 'startColumnIndex': 2}],
                    'booleanRule': {
                        'condition': {'type': 'TEXT_CONTAINS', 'values': [{'userEnteredValue': 'Ожидается'}]},
                        'format': {
                            'backgroundColor': {'red': 1.0, 'green': 0.95, 'blue': 0.80},
                            'textFormat': {'foregroundColor': {'red': 0.50, 'green': 0.38, 'blue': 0.0}}
                        }
                    }
                },
                'index': 1
            }
        },
        # Красный — Не будет
        {
            'addConditionalFormatRule': {
                'rule': {
                    'ranges': [{'sheetId': sheet_id, 'startRowIndex': 1, 'startColumnIndex': 2}],
                    'booleanRule': {
                        'condition': {'type': 'TEXT_CONTAINS', 'values': [{'userEnteredValue': 'Не будет'}]},
                        'format': {
                            'backgroundColor': {'red': 0.99, 'green': 0.90, 'blue': 0.80},
                            'textFormat': {'foregroundColor': {'red': 0.47, 'green': 0.25, 'blue': 0.02}}
                        }
                    }
                },
                'index': 2
            }
        },
        # Серый — Не отметился
        {
            'addConditionalFormatRule': {
                'rule': {
                    'ranges': [{'sheetId': sheet_id, 'startRowIndex': 1, 'startColumnIndex': 2}],
                    'booleanRule': {
                        'condition': {'type': 'TEXT_CONTAINS', 'values': [{'userEnteredValue': 'Не отметился'}]},
                        'format': {
                            'backgroundColor': {'red': 0.94, 'green': 0.94, 'blue': 0.94},
                            'textFormat': {'foregroundColor': {'red': 0.45, 'green': 0.45, 'blue': 0.45}}
                        }
                    }
                },
                'index': 3
            }
        }
    ]
    try:
        spreadsheet.batch_update({'requests': rules})
    except Exception as e:
        logger.warning(f"Не удалось применить условное форматирование: {e}")

def get_or_create_month_worksheet(spreadsheet, target_date: str):
    """Возвращает лист текущего месяца (например 'Август 2026') или создает его"""
    month_title = get_month_sheet_title(target_date)
    try:
        ws = spreadsheet.worksheet(month_title)
        return ws
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=month_title, rows=100, cols=40)
        apply_conditional_formatting(spreadsheet, ws.id)
        return ws

def sync_rollcall_to_sheet(sheet_url: str, target_date: str, votes: List[Tuple], known_users: List[Tuple] = None) -> Optional[str]:
    """
    votes: list of (user_id, username, full_name, status, checked_in, [checkin_time])
    Синхронизирует результаты переклички в Google Таблицу на вкладку месяца.
    """
    if not sheet_url:
        return None
    
    client = get_gspread_client()
    if not client:
        return "Не удалось авторизоваться в Google API (проверьте файл ключа)"

    try:
        spreadsheet = client.open_by_url(sheet_url)
        worksheet = get_or_create_month_worksheet(spreadsheet, target_date)

        # 1. Считываем всю таблицу за 1 сетевой запрос
        all_values = worksheet.get_all_values()

        if not all_values or not all_values[0] or all_values[0][0] != "Имя участника":
            headers = ["Имя участника", "Telegram"]
            user_rows = []
            if known_users:
                for uid, uname, fname in known_users:
                    user_rows.append([fname, f"@{uname}" if uname else ""])
        else:
            headers = list(all_values[0])
            user_rows = all_values[1:]

        # Исключаем любые строки «ИТОГО ВЫШЛО» из списка участников
        user_rows = [r for r in user_rows if not (len(r) > 0 and "ИТОГО" in r[0].upper())]

        # 2. Добавляем колонку с датой, если ее еще нет
        if target_date not in headers:
            headers.append(target_date)

        date_col_idx = headers.index(target_date)
        num_cols = len(headers)

        # 3. Индексируем существующих участников
        user_row_map = {}
        for idx, row in enumerate(user_rows):
            name = row[0] if len(row) > 0 else ""
            username = row[1] if len(row) > 1 else ""
            if username:
                user_row_map[normalize_key(username)] = idx
            if name:
                user_row_map[normalize_key(name)] = idx

        # 4. Формируем маппинг активных голосов
        active_votes_map = {}
        for item in votes:
            user_id = item[0]
            username = item[1]
            full_name = item[2]
            status = item[3]
            checked_in = item[4]
            checkin_time = item[5] if len(item) > 5 else None

            status_text = status
            if status == '+' and checked_in:
                time_str = f" {checkin_time}" if checkin_time else ""
                status_text = f"+ (Пришел{time_str})"
            elif status == '+' and not checked_in:
                status_text = "+ (Ожидается)"
            elif status == '-':
                status_text = "- (Не будет)"

            cell_value = status_text
            
            key = normalize_key(username) if username else normalize_key(full_name)
            active_votes_map[key] = (cell_value, full_name, username)

        # 5. Обновляем строки существующих пользователей
        matched_keys = set()
        for idx, row in enumerate(user_rows):
            while len(row) < num_cols:
                row.append("Не отметился")

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

            user_rows[idx] = row

        # 6. Добавляем новых участников
        for key, (cell_value, full_name, username) in active_votes_map.items():
            if key not in matched_keys:
                new_row = [full_name, f"@{username}" if username else ""]
                while len(new_row) < num_cols:
                    new_row.append("Не отметился")
                new_row[date_col_idx] = cell_value
                user_rows.append(new_row)
                matched_keys.add(key)

        # 7. Формируем строку «ИТОГО ВЫШЛО» (подсчет числа вышедших на каждую дату)
        summary_row = ["ИТОГО ВЫШЛО", ""]
        for c_idx in range(2, num_cols):
            present_count = 0
            for r in user_rows:
                if len(r) > c_idx and "Пришел" in r[c_idx]:
                    present_count += 1
            summary_row.append(str(present_count))

        # 8. Собираем итоговую матрицу
        final_matrix = [headers] + user_rows + [summary_row]

        # 9. Записываем ВСЮ таблицу за 1 пакетный запрос как чистый текст (RAW)
        worksheet.update(final_matrix, "A1", value_input_option="RAW")
        logger.info(f"Успешно синхронизировано в Google Sheets (лист {worksheet.title}) для даты {target_date}")
        return None
    except Exception as e:
        err_msg = f"Ошибка записи в Google Sheets: {e}"
        logger.error(err_msg)
        return err_msg

async def async_sync_rollcall_to_sheet(sheet_url: str, target_date: str, votes: List[Tuple], bot=None, chat_id: int = None, known_users: List[Tuple] = None):
    async with _sync_lock:
        error = await asyncio.to_thread(sync_rollcall_to_sheet, sheet_url, target_date, votes, known_users)
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
