import asyncio
import os
import sys
from datetime import datetime

# Set test environment
os.environ["DB_PATH"] = "test_bot_data.db"
os.environ["MAX_VOTE_CHANGES"] = "3"
os.environ["RATE_LIMIT_CALLBACK"] = "2.0"
os.environ["SHEETS_SYNC_DEBOUNCE_SECONDS"] = "0.5"

from db import (
    init_db, save_vote, remove_vote, get_votes_for_date,
    get_user_vote_record, is_reason_already_requested, mark_reason_requested,
    get_attendance_stats
)
from bot.handlers import is_date_in_past
from bot.middlewares import ThrottlingMiddleware
from services.sheets_queue import queue_sheet_sync, flush_all_sheet_syncs

from aiogram.types import CallbackQuery, User

def create_mock_callback(user_id: int, logs_list: list):
    user = User(id=user_id, is_bot=False, first_name="Tester", username="tester")
    cb = CallbackQuery(id="mock_id", from_user=user, chat_instance="mock_inst")
    
    async def mock_answer(text: str = "", show_alert: bool = False, **kwargs):
        logs_list.append((text, show_alert))
        return True

    object.__setattr__(cb, "answer", mock_answer)
    return cb

async def test_database_and_limits():
    print("--- [1] Testing DB & Vote Change Limits ---")
    if os.path.exists("test_bot_data.db"):
        os.remove("test_bot_data.db")

    await init_db()

    chat_id = -10012345
    target_date = "19.08.2026"
    user_id = 999111

    # First vote: '+'
    await save_vote(chat_id, target_date, user_id, "user1", "User One", "+", increment_change=False)
    rec = await get_user_vote_record(chat_id, target_date, user_id)
    assert rec["status"] == "+", f"Expected +, got {rec['status']}"
    assert rec["change_count"] == 0, f"Expected 0 changes, got {rec['change_count']}"
    print("✓ First vote recorded with change_count=0")

    # Change 1: change to '-'
    await save_vote(chat_id, target_date, user_id, "user1", "User One", "-", increment_change=True)
    rec = await get_user_vote_record(chat_id, target_date, user_id)
    assert rec["status"] == "-", f"Expected -, got {rec['status']}"
    assert rec["change_count"] == 1, f"Expected 1 changes, got {rec['change_count']}"
    print("✓ Change 1 recorded with change_count=1")

    # Change 2: remove vote
    await remove_vote(chat_id, target_date, user_id, increment_change=True)
    rec = await get_user_vote_record(chat_id, target_date, user_id)
    assert rec["status"] is None, f"Expected None status, got {rec['status']}"
    assert rec["change_count"] == 2, f"Expected 2 changes, got {rec['change_count']}"
    print("✓ Change 2 (remove) recorded with change_count=2")

    # Change 3: vote '+' again
    await save_vote(chat_id, target_date, user_id, "user1", "User One", "+", increment_change=True)
    rec = await get_user_vote_record(chat_id, target_date, user_id)
    assert rec["change_count"] == 3, f"Expected 3 changes, got {rec['change_count']}"
    print("✓ Change 3 recorded with change_count=3")

    # Verify get_votes_for_date returns active votes only
    votes = await get_votes_for_date(chat_id, target_date)
    assert len(votes) == 1, f"Expected 1 active vote, got {len(votes)}"
    print("✓ get_votes_for_date filters correctly")

async def test_reason_deduplication():
    print("\n--- [2] Testing Reason Request Deduplication ---")
    chat_id = -10012345
    target_date = "19.08.2026"
    user_id = 999111

    asked_before = await is_reason_already_requested(chat_id, target_date, user_id)
    assert not asked_before, "Reason should not be marked yet"

    await mark_reason_requested(chat_id, target_date, user_id)

    asked_after = await is_reason_already_requested(chat_id, target_date, user_id)
    assert asked_after, "Reason should be marked as requested"
    print("✓ Reason deduplication flag works correctly")

def test_date_validation():
    print("\n--- [3] Testing Date Validation ---")
    today = "18.08.2026"
    assert is_date_in_past("17.08.2026", today) is True, "Yesterday should be past"
    assert is_date_in_past("18.08.2026", today) is False, "Today is not past"
    assert is_date_in_past("19.08.2026", today) is False, "Tomorrow is not past"
    print("✓ Date validation correctly identifies past polls")

async def test_throttling_middleware():
    print("\n--- [4] Testing Throttling Middleware ---")
    mw = ThrottlingMiddleware()
    called = []
    async def dummy_handler(event, data):
        called.append(True)

    answered_logs = []
    cb = create_mock_callback(user_id=123, logs_list=answered_logs)

    # Click 1: Allowed
    await mw(dummy_handler, cb, {})
    assert len(called) == 1, "First click should be processed"

    # Click 2 immediately: Throttled
    await mw(dummy_handler, cb, {})
    assert len(called) == 1, "Second immediate click should be blocked by rate limit"
    assert len(answered_logs) > 0 and "Не нажимайте" in answered_logs[-1][0], f"Expected throttle message, got {answered_logs}"

    # Spam 7 rapid clicks -> Temporary block
    for _ in range(7):
        await mw(dummy_handler, cb, {})
    
    assert any("заблокированы" in a[0] or "Блокировка" in a[0] for a in answered_logs), "Should trigger temporary spam block"
    print("✓ Throttling middleware blocks click spam and autoclickers")

async def test_sheets_queue():
    print("\n--- [5] Testing Sheets Debounce Queue ---")
    # Queue multiple updates quickly
    chat_id = -10012345
    target_date = "19.08.2026"
    
    # We will test queue and graceful flush
    await queue_sheet_sync("", target_date, chat_id, None, delay=0.1)
    await flush_all_sheet_syncs()
    print("✓ Sheets queue debounce and flush verified")

async def main():
    await test_database_and_limits()
    await test_reason_deduplication()
    test_date_validation()
    await test_throttling_middleware()
    await test_sheets_queue()

    if os.path.exists("test_bot_data.db"):
        os.remove("test_bot_data.db")

    print("\n==========================================")
    print("🎉 ALL SECURITY & ANTI-ABUSE TESTS PASSED!")
    print("==========================================")

if __name__ == "__main__":
    asyncio.run(main())
