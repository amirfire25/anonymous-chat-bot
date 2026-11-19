
import random

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

TOKEN = "YOUR_BOT_TOKEN"

# آمار موقت کاربران
stats = {}

# -------------------------
# شروع ربات
# -------------------------

def main_menu():
    keyboard = [
        [InlineKeyboardButton("🎮 شروع بازی", callback_data="new")],
        [InlineKeyboardButton("🏆 آمار من", callback_data="stats")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎮 به بازی دوز خوش اومدی!\n\n"
        "من رقیب تو هستم. آماده‌ای منو شکست بدی؟ 😎",
        reply_markup=main_menu()
    )

# -------------------------
# منوی انتخاب علامت
# -------------------------

async def choose_symbol(query):
    keyboard = [
        [
            InlineKeyboardButton("❌ ضربدر", callback_data="symbol_X"),
            InlineKeyboardButton("⭕ دایره", callback_data="symbol_O")
        ],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="home")]
    ]

    await query.edit_message_text(
        "علامت خودت رو انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# -------------------------
# انتخاب سختی
# -------------------------

async def choose_difficulty(query, symbol):
    keyboard = [
        [InlineKeyboardButton("🟢 آسان", callback_data=f"level_easy_{symbol}")],
        [InlineKeyboardButton("🟡 متوسط", callback_data=f"level_medium_{symbol}")],
        [InlineKeyboardButton("🔴 سخت (غیرقابل شکست)", callback_data=f"level_hard_{symbol}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="new")]
    ]

    await query.edit_message_text(
        "سطح سختی بازی رو انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# -------------------------
# منطق بازی
# -------------------------

WIN_LINES = [
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    (0, 4, 8),
    (2, 4, 6)
]

def winner(board):
    for a, b, c in WIN_LINES:
        if board[a] != " " and board[a] == board[b] == board[c]:
            return board[a]

    return None

def available(board):
    return [i for i, cell in enumerate(board) if cell == " "]

def minimax(board, ai, player, maximizing):
    result = winner(board)

    if result == ai:
        return 10
    if result == player:
        return -10
    if not available(board):
        return 0

    if maximizing:
        best = -100

        for move in available(board):
            board[move] = ai
            score = minimax(board, ai, player, False)
            board[move] = " "
            best = max(best, score)

        return best

    best = 100

    for move in available(board):
        board[move] = player
        score = minimax(board, ai, player, True)
        board[move] = " "
        best = min(best, score)

    return best

def ai_move(board, ai, player, difficulty):
    moves = available(board)

    if not moves:
        return None

    # آسان
    if difficulty == "easy":
        return random.choice(moves)

    # حرکت برد
    for move in moves:
        board[move] = ai
        if winner(board) == ai:
            board[move] = " "
            return move
        board[move] = " "

    # جلوگیری از برد بازیکن
    for move in moves:
        board[move] = player
        if winner(board) == player:
            board[move] = " "
            return move
        board[move] = " "

    # متوسط
    if difficulty == "medium":
        return random.choice(moves)

    # سخت: الگوریتم Minimax
    best_score = -100
    best_moves = []

    for move in moves:
        board[move] = ai
        score = minimax(board, ai, player, False)
        board[move] = " "

        if score > best_score:
            best_score = score
            best_moves = [move]
        elif score == best_score:
            best_moves.append(move)

    return random.choice(best_moves)

# -------------------------
# ساخت صفحه بازی
# -------------------------

def board_keyboard(board, finished=False):
    keyboard = []

    for row in range(3):
        buttons = []

        for col in range(3):
            i = row * 3 + col
            cell = board[i]

            if cell == "X":
                label = "❌"
            elif cell == "O":
                label = "⭕"
            else:
                label = "▫️"

            callback = "noop" if finished or cell != " " else f"move_{i}"

            buttons.append(
                InlineKeyboardButton(label, callback_data=callback)
            )

        keyboard.append(buttons)

    if finished:
        keyboard.append([
            InlineKeyboardButton("🔄 بازی مجدد", callback_data="new"),
            InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")
        ])

    return InlineKeyboardMarkup(keyboard)

def game_text(game):
    player = game["player"]
    ai = game["ai"]

    return (
        "🎮 بازی دوز\n\n"
        f"علامت تو: {'❌' if player == 'X' else '⭕'}\n"
        f"علامت من: {'❌' if ai == 'X' else '⭕'}\n\n"
        "نوبت توئه! یک خانه انتخاب کن."
    )

def get_stats(user_id):
    return stats.setdefault(
        user_id,
        {"win": 0, "lose": 0, "draw": 0}
    )

def finish_game(game, user_id):
    result = winner(game["board"])
    player = game["player"]
    ai = game["ai"]

    if result == player:
        get_stats(user_id)["win"] += 1
        return "🎉 آفرین! تو برنده شدی!"

    if result == ai:
        get_stats(user_id)["lose"] += 1
        return "🤖 این بار من بردم! دوباره تلاش کن."

    get_stats(user_id)["draw"] += 1
    return "🤝 بازی مساوی شد!"

# -------------------------
# مدیریت دکمه‌ها
# -------------------------

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    games = context.application.bot_data.setdefault("games", {})

    if data == "noop":
        return

    if data == "home":
        games.pop(user_id, None)
        await query.edit_message_text(
            "🎮 منوی اصلی",
            reply_markup=main_menu()
        )
        return

    if data == "new":
        games.pop(user_id, None)
        await choose_symbol(query)
        return

    if data == "stats":
        s = get_stats(user_id)

        await query.edit_message_text(
            "🏆 آمار بازی‌های تو\n\n"
            f"🟢 برد: {s['win']}\n"
            f"🔴 باخت: {s['lose']}\n"
            f"🟡 مساوی: {s['draw']}\n"
            f"🎮 مجموع: {s['win'] + s['lose'] + s['draw']}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="home")]
            ])
        )
        return

    if data.startswith("symbol_"):
        symbol = data.split("_")[1]
        await choose_difficulty(query, symbol)
        return

    if data.startswith("level_"):
        _, difficulty, symbol = data.split("_")

        game = {
            "board": [" "] * 9,
            "player": symbol,
            "ai": "O" if symbol == "X" else "X",
            "difficulty": difficulty
        }

        games[user_id] = game

        await query.edit_message_text(
            game_text(game),
            reply_markup=board_keyboard(game["board"])
        )
        return

    if data.startswith("move_"):
        game = games.get(user_id)

        if not game:
            await query.edit_message_text(
                "بازی فعالی پیدا نشد.",
                reply_markup=main_menu()
            )
            return

        board = game["board"]
        move = int(data.split("_")[1])

        if move < 0 or move > 8 or board[move] != " ":
            return

        # حرکت بازیکن
        board[move] = game["player"]

        if winner(board) or not available(board):
            result = finish_game(game, user_id)
            await query.edit_message_text(
                result,
                reply_markup=board_keyboard(board, True)
            )
            return

        # حرکت ربات
        ai = ai_move(
            board,
            game["ai"],
            game["player"],
            game["difficulty"]
        )

        if ai is not None:
            board[ai] = game["ai"]

        if winner(board) or not available(board):
            result = finish_game(game, user_id)
            await query.edit_message_text(
                result,
                reply_markup=board_keyboard(board, True)
            )
            return

        await query.edit_message_text(
            game_text(game),
            reply_markup=board_keyboard(board)
        )

# -------------------------
# اجرای ربات
# -------------------------

def main():
    if TOKEN == "YOUR_BOT_TOKEN":
        raise ValueError("Please enter your Telegram bot token.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))

    print("Tic Tac Toe Bot is running...")

    app.run_polling()

if __name__ == "__main__":
    main()
