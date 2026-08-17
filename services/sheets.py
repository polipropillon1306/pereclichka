import os
import logging
import asyncio
from datetime import datetime
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
        # 1. Желтый — Ожидается
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
                'index': 0
            }
        },
        # 2. Красный — Не будет
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
                'index': 1
            }
        },
        # 3. Серый — Не отметился
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
                'index': 2
            }
        },
        # 4. Зеленый — Пришел / интервал работы / подтвержденный выход
        {
            'addConditionalFormatRule': {
                'rule': {
                    'ranges': [{'sheetId': sheet_id, 'startRowIndex': 1, 'startColumnIndex': 2}],
                    'booleanRule': {
                        'condition': {'type': 'TEXT_STARTS_WITH', 'values': [{'userEnteredValue': '+'}]},
                        'format': {
                            'backgroundColor': {'red': 0.85, 'green': 0.92, 'blue': 0.83},
                            'textFormat': {'foregroundColor': {'red': 0.15, 'green': 0.31, 'blue': 0.07}, 'bold': True}
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
    votes: list of (user_id, username, full_name, status, checked_in, [checkin_time], [checkout_time])
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

        # 2. Формируем отсортированный по календарю список дат
        existing_date_headers = list(headers[2:])
        date_headers = list(dict.fromkeys(existing_date_headers))
        if target_date not in date_headers:
            date_headers.append(target_date)

        def parse_date_key(d_str):
            try:
                return datetime.strptime(d_str.strip(), "%d.%m.%Y")
            except Exception:
                return datetime.max

        date_headers.sort(key=parse_date_key)
        new_headers = ["Имя участника", "Telegram"] + date_headers
        num_cols = len(new_headers)

        # 3. Индексируем существующих участников и их историю по датам
        user_date_data = []
        user_row_map = {}
        for idx, row in enumerate(user_rows):
            name = row[0] if len(row) > 0 else ""
            username = row[1] if len(row) > 1 else ""
            d_vals = {}
            for col_i, d in enumerate(existing_date_headers, start=2):
                if col_i < len(row):
                    d_vals[d] = row[col_i]
            
            entry = {"name": name, "username": username, "dates": d_vals}
            user_date_data.append(entry)
            
            if username:
                user_row_map[normalize_key(username)] = idx
            if name:
                user_row_map[normalize_key(name)] = idx

        # 4. Формируем маппинг активных голосов за целевую дату
        active_votes_map = {}
        for item in votes:
            user_id = item[0]
            username = item[1]
            full_name = item[2]
            status = item[3]
            checked_in = item[4]
            checkin_time = item[5] if len(item) > 5 else None
            checkout_time = item[6] if len(item) > 6 else None

            status_text = status
            if status == '+' and checked_in:
                if checkin_time and checkout_time:
                    status_text = f"+ ({checkin_time} — {checkout_time})"
                elif checkin_time:
                    status_text = f"+ (Пришел {checkin_time})"
                elif checkout_time:
                    status_text = f"+ (Ушел {checkout_time})"
                else:
                    status_text = "+ (Пришел)"
            elif status == '+' and not checked_in:
                status_text = "+ (Ожидается)"
            elif status == '-':
                status_text = "- (Не будет)"

            cell_value = status_text
            key = normalize_key(username) if username else normalize_key(full_name)
            active_votes_map[key] = (cell_value, full_name, username)

        # 5. Обновляем строки существующих пользователей
        matched_keys = set()
        for idx, entry in enumerate(user_date_data):
            u_key = normalize_key(entry["username"]) if entry["username"] else None
            n_key = normalize_key(entry["name"]) if entry["name"] else None

            found_key = None
            if u_key and u_key in active_votes_map:
                found_key = u_key
            elif n_key and n_key in active_votes_map:
                found_key = n_key

            if found_key:
                matched_keys.add(found_key)
                entry["dates"][target_date] = active_votes_map[found_key][0]
                if active_votes_map[found_key][1]:
                    entry["name"] = active_votes_map[found_key][1]
                if active_votes_map[found_key][2]:
                    entry["username"] = f"@{active_votes_map[found_key][2]}"
            else:
                if target_date not in entry["dates"]:
                    entry["dates"][target_date] = "Не отметился"

        # 6. Добавляем новых участников, которых еще не было в таблице
        for key, (cell_value, full_name, username) in active_votes_map.items():
            if key not in matched_keys:
                new_entry = {
                    "name": full_name,
                    "username": f"@{username}" if username else "",
                    "dates": {target_date: cell_value}
                }
                user_date_data.append(new_entry)
                matched_keys.add(key)

        # 7. Формируем строки участников в правильном порядке столбцов
        final_user_rows = []
        for entry in user_date_data:
            row = [entry["name"], entry["username"]]
            for d in date_headers:
                row.append(entry["dates"].get(d, "Не отметился"))
            final_user_rows.append(row)

        # 8. Формируем строку «ИТОГО ВЫШЛО» (подсчет числа вышедших на каждую дату)
        summary_row = ["ИТОГО ВЫШЛО", ""]
        for c_idx in range(2, num_cols):
            present_count = 0
            for r in final_user_rows:
                if len(r) > c_idx and r[c_idx].startswith("+ (") and "Ожидается" not in r[c_idx]:
                    present_count += 1
            summary_row.append(str(present_count))

        # 9. Собираем итоговую матрицу
        final_matrix = [new_headers] + final_user_rows + [summary_row]

        # 10. Записываем ВСЮ таблицу за 1 пакетный запрос как чистый текст (RAW)
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
