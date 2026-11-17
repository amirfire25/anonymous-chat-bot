import asyncio
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

# -------------------- تنظیمات --------------------

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "").split(",")
    if value.strip().isdigit()
}

DB_PATH = os.getenv("DB_PATH", "data/database.sqlite")
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("anonymous-chat")

router = Router()


# -------------------- دیتابیس --------------------

class Database:
    def __init__(self, path: str):
        self.path = path

    async def connect(self):
        db = await aiosqlite.connect(self.path, timeout=30)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute("PRAGMA foreign_keys=ON")
        return db

    async def init(self):
        async with await self.connect() as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT DEFAULT '',
                    first_name TEXT DEFAULT '',
                    joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    is_banned INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS queue (
                    user_id INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                    joined_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    user1_id INTEGER PRIMARY KEY REFERENCES users(user_id),
                    user2_id INTEGER NOT NULL UNIQUE REFERENCES users(user_id),
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    CHECK(user1_id < user2_id)
                );

                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reporter_id INTEGER NOT NULL,
                    reported_id INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'new'
                );
            """)
            await db.commit()

    async def add_user(self, user_id, username="", first_name=""):
        async with self.connect() as db:
            await db.execute(
                """INSERT INTO users(user_id, username, first_name)
                   VALUES(?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                   username=excluded.username,
                   first_name=excluded.first_name""",
                (user_id, username, first_name),
            )
            await db.commit()

    async def is_banned(self, user_id):
        async with await self.connect() as db:
            cur = await db.execute(
                "SELECT is_banned FROM users WHERE user_id=?", (user_id,)
            )
            row = await cur.fetchone()
            return bool(row and row["is_banned"])

    async def set_ban(self, user_id, banned):
        async with await self.connect() as db:
            await db.execute(
                "UPDATE users SET is_banned=? WHERE user_id=?",
                (int(banned), user_id),
            )
            await db.execute("DELETE FROM queue WHERE user_id=?", (user_id,))
            cur = await db.execute(
                """SELECT user1_id, user2_id FROM sessions
                   WHERE user1_id=? OR user2_id=?""",
                (user_id, user_id),
            )
            row = await cur.fetchone()
            await db.execute(
                "DELETE FROM sessions WHERE user1_id=? OR user2_id=?",
                (user_id, user_id),
            )
            await db.commit()
            if row:
                return row["user2_id"] if row["user1_id"] == user_id else row["user1_id"]
            return None

    async def match_or_queue(self, user_id):
        # قفل تراکنش از مچ‌شدن هم‌زمان یک کاربر با دو نفر جلوگیری می‌کند.
        async with await self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")

            cur = await db.execute(
                "SELECT is_banned FROM users WHERE user_id=?", (user_id,)
            )
            row = await cur.fetchone()
            if not row or row["is_banned"]:
                await db.rollback()
                return "banned", None

            cur = await db.execute(
                """SELECT 1 FROM sessions
                   WHERE user1_id=? OR user2_id=?""",
                (user_id, user_id),
            )
            if await cur.fetchone():
                await db.rollback()
                return "busy", None

            await db.execute("DELETE FROM queue WHERE user_id=?", (user_id,))
            cur = await db.execute(
                """
                SELECT q.user_id
                FROM queue q
                JOIN users u ON u.user_id=q.user_id
                WHERE q.user_id != ?
                  AND u.is_banned=0
                  AND NOT EXISTS (
                      SELECT 1 FROM sessions s
                      WHERE s.user1_id=q.user_id OR s.user2_id=q.user_id
                  )
                ORDER BY q.joined_at ASC
                LIMIT 1
                """,
                (user_id,),
            )
            partner_row = await cur.fetchone()

            if not partner_row:
                await db.execute(
                    "INSERT INTO queue(user_id) VALUES(?)", (user_id,)
                )
                await db.commit()
                return "queued", None

            partner = int(partner_row["user_id"])
            user1, user2 = sorted((user_id, partner))
            await db.execute(
                "DELETE FROM queue WHERE user_id IN (?,?)",
                (user1, user2),
            )
            await db.execute(
                "INSERT INTO sessions(user1_id,user2_id) VALUES(?,?)",
                (user1, user2),
            )
            await db.commit()
            return "matched", partner

    async def cancel_queue(self, user_id):
        async with await self.connect() as db:
            await db.execute("DELETE FROM queue WHERE user_id=?", (user_id,))
            await db.commit()

    async def get_partner(self, user_id):
        async with await self.connect() as db:
            cur = await db.execute(
                """SELECT user1_id,user2_id FROM sessions
                   WHERE user1_id=? OR user2_id=?""",
                (user_id, user_id),
            )
            row = await cur.fetchone()
            if not row:
                return None
            return row["user2_id"] if row["user1_id"] == user_id else row["user1_id"]

    async def end_session(self, user_id):
        async with await self.connect() as db:
            cur = await db.execute(
                """SELECT user1_id,user2_id FROM sessions
                   WHERE user1_id=? OR user2_id=?""",
                (user_id, user_id),
            )
            row = await cur.fetchone()
            if not row:
                return None
            partner = row["user2_id"] if row["user1_id"] == user_id else row["user1_id"]
            await db.execute(
                "DELETE FROM sessions WHERE user1_id=? OR user2_id=?",
                (user_id, user_id),
            )
            await db.commit()
            return partner

    async def add_report(self, reporter, reported):
        async with await self.connect() as db:
            cur = await db.execute(
                "INSERT INTO reports(reporter_id,reported_id) VALUES(?,?)",
                (reporter, reported),
            )
            await db.commit()
            return cur.lastrowid

    async def get_stats(self):
        async with await self.connect() as db:
            async def count(sql):
                cur = await db.execute(sql)
                row = await cur.fetchone()
                return row[0]
            return (
                await count("SELECT COUNT(*) FROM users"),
                await count("SELECT COUNT(*) FROM sessions"),
                await count("SELECT COUNT(*) FROM queue"),
                await count("SELECT COUNT(*) FROM reports WHERE status='new'"),
            )


db = Database(DB_PATH)


# -------------------- منوها --------------------

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 جستجوی هم‌صحبت")],
            [KeyboardButton(text="📊 آمار ربات"), KeyboardButton(text="ℹ️ راهنما")],
            [KeyboardButton(text="📞 پشتیبانی")],
        ],
        resize_keyboard=True,
    )


def chat_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⏭️ نفر بعدی"), KeyboardButton(text="🛑 پایان گفتگو")],
            [KeyboardButton(text="⚠️ گزارش تخلف")],
        ],
        resize_keyboard=True,
    )


def is_admin(message: Message):
    return bool(message.from_user and message.from_user.id in ADMIN_IDS)


# -------------------- محدودکننده پیام --------------------

class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, limit=0.5):
        self.limit = limit
        self.last_message = {}

    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.from_user:
            # دکمه‌های مدیریتی و دستورات هم مانند بقیه پیام‌ها کنترل می‌شوند.
            now = asyncio.get_running_loop().time()
            user_id = event.from_user.id
            if now - self.last_message.get(user_id, 0) < self.limit:
                return None
            self.last_message[user_id] = now
        return await handler(event, data)


# -------------------- شروع و راهنما --------------------

@router.message(CommandStart())
async def start_handler(message: Message):
    user = message.from_user
    await db.add_user(
        user.id,
        user.username or "",
        user.first_name or "",
    )
    if await db.is_banned(user.id):
        return await message.answer("⛔ حساب شما مسدود شده است.")
    await message.answer(
        "سلام! به چت ناشناس خوش اومدی.\nبرای پیدا کردن هم‌صحبت روی جستجو بزن.",
        reply_markup=main_menu(),
    )


@router.message(F.text == "ℹ️ راهنما")
async def help_handler(message: Message):
    await message.answer(
        "راهنما:\n"
        "۱) جستجوی هم‌صحبت را بزن.\n"
        "۲) پیام‌هایت برای طرف مقابل ناشناس ارسال می‌شود.\n"
        "۳) برای پایان گفتگو /stop و برای نفر بعدی /next را بزن.\n"
        "۴) با دکمه گزارش تخلف می‌توانی گفتگو را گزارش کنی."
    )


@router.message(F.text == "📞 پشتیبانی")
async def support_handler(message: Message):
    if ADMIN_IDS:
        admins = ", ".join(f"<code>{admin_id}</code>" for admin_id in sorted(ADMIN_IDS))
        await message.answer(
            "برای پشتیبانی به یکی از مدیران پیام بده:\n" + admins,
            parse_mode="HTML",
        )
    else:
        await message.answer("اطلاعات پشتیبانی هنوز توسط مدیر تنظیم نشده است.")


# -------------------- جستجو و گفتگو --------------------

async def search_partner(message: Message):
    status, partner = await db.match_or_queue(message.from_user.id)

    if status == "banned":
        return await message.answer("⛔ حساب شما مسدود شده است.")
    if status == "busy":
        return await message.answer(
            "شما همین الان در یک گفتگو هستید.",
            reply_markup=chat_menu(),
        )
    if status == "queued":
        return await message.answer(
            "⏳ در صف جستجو قرار گرفتی. برای لغو جستجو /cancel را بزن.",
            reply_markup=main_menu(),
        )

    await message.answer(
        "🎉 هم‌صحبت پیدا شد! گفتگو شروع شد.",
        reply_markup=chat_menu(),
    )
    try:
        await message.bot.send_message(
            partner,
            "🎉 هم‌صحبت پیدا شد! گفتگو شروع شد.",
            reply_markup=chat_menu(),
        )
    except Exception:
            # اگر ارسال پیام به طرف مقابل ممکن نشد، جلسه پاک می‌شود.
        await db.end_session(message.from_user.id)
        await message.answer(
            "طرف مقابل در دسترس نیست؛ دوباره جستجو کن.",
            reply_markup=main_menu(),
        )


@router.message(F.text == "🔍 جستجوی هم‌صحبت")
@router.message(Command("search"))
async def start_search_handler(message: Message):
    user = message.from_user
    await db.add_user(user.id, user.username or "", user.first_name or "")
    await search_partner(message)


@router.message(Command("cancel"))
@router.message(F.text == "❌ لغو جستجو")
async def cancel_search_handler(message: Message):
    await db.cancel_queue(message.from_user.id)
    await message.answer("جستجو لغو شد.", reply_markup=main_menu())


@router.message(F.text.in_({"🛑 پایان گفتگو", "⏭️ نفر بعدی"}))
@router.message(Command("stop"))
@router.message(Command("next"))
async def stop_or_next_handler(message: Message):
    user_id = message.from_user.id
    is_next = message.text in ("⏭️ نفر بعدی", "/next")

    partner = await db.end_session(user_id)
    if not partner:
        await db.cancel_queue(user_id)
        return await message.answer(
            "گفتگوی فعالی نداری.",
            reply_markup=main_menu(),
        )

    await message.answer("گفتگو پایان یافت.", reply_markup=main_menu())
    try:
        await message.bot.send_message(
            partner,
            "طرف مقابل گفتگو را ترک کرد.",
            reply_markup=main_menu(),
        )
    except Exception:
        pass

    if is_next:
        await search_partner(message)


@router.message(F.text == "⚠️ گزارش تخلف")
async def report_handler(message: Message):
    user_id = message.from_user.id
    partner = await db.get_partner(user_id)
    if not partner:
        return await message.answer("برای گزارش باید داخل گفتگو باشی.")

    report_id = await db.add_report(user_id, partner)
    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_message(
                admin_id,
                f"🚨 گزارش جدید #{report_id}\n"
                f"گزارش‌دهنده: <code>{user_id}</code>\n"
                f"کاربر گزارش‌شده: <code>{partner}</code>",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not notify admin %s about report", admin_id)

    await message.answer("گزارشت برای مدیران ارسال و در پایگاه داده ثبت شد.")


# -------------------- دستورات مدیر --------------------

@router.message(Command("ban"))
async def ban_handler(message: Message):
    if not is_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer("فرمت صحیح: /ban USER_ID")

    target = int(parts[1])
    partner = await db.set_ban(target, True)
    if partner:
        try:
            await message.bot.send_message(
                partner,
                "گفتگوی شما به پایان رسید.",
                reply_markup=main_menu(),
            )
        except Exception:
            pass
    await message.answer(f"کاربر {target} مسدود شد.")


@router.message(Command("unban"))
async def unban_handler(message: Message):
    if not is_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer("فرمت صحیح: /unban USER_ID")

    await db.set_ban(int(parts[1]), False)
    await message.answer("رفع مسدودی انجام شد.")


@router.message(Command("stats"))
@router.message(F.text == "📊 آمار ربات")
async def stats_handler(message: Message):
    if not is_admin(message):
        return await message.answer("این بخش مخصوص مدیر ربات است.")

    users, sessions, queue_count, reports = await db.get_stats()
    await message.answer(
        "📊 آمار ربات\n"
        f"👥 کاربران: {users}\n"
        f"💬 گفتگوهای فعال: {sessions}\n"
        f"⏳ افراد منتظر: {queue_count}\n"
        f"🚨 گزارش‌های بررسی‌نشده: {reports}"
    )


# -------------------- انتقال پیام ناشناس --------------------

@router.message()
async def forward_message(message: Message):
    if not message.from_user:
        return
    if await db.is_banned(message.from_user.id):
        return

    partner = await db.get_partner(message.from_user.id)
    if not partner:
        return

    try:
        # copy_to هویت فرستنده را در پیام کپی‌شده نمایش نمی‌دهد.
        await message.copy_to(partner)
    except Exception:
        await message.answer(
            "ارسال پیام انجام نشد. احتمالاً طرف مقابل ربات را متوقف کرده است."
        )


# -------------------- اجرای ربات --------------------

async def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "متغیر TELEGRAM_BOT_TOKEN را در تنظیمات Railway وارد کن."
        )

    await db.init()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.message.middleware(ThrottlingMiddleware(0.5))
    dp.include_router(router)

    logger.info("Bot started")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
