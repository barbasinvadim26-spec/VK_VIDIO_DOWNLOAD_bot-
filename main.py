
import os
import re
import time
import sqlite3
import calendar
import threading
import datetime
import telebot

from telebot import types
from downloader import download_video

# =====================================
# НАСТРОЙКИ
# =====================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

YOOMONEY_WALLET = "4100119679280686"
ADMIN_ID = 12345678

PRICE = 5
DB_NAME = "bot_database.db"

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN!")

bot = telebot.TeleBot(BOT_TOKEN)

db_lock = threading.Lock()
download_lock = threading.Lock()

SUPPORTED_PLATFORMS = [
    "vk.com",
    "vkvideo.ru",
    "m.vkvideo.ru",
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "rutube.ru",
    "likee.video"
]


# =====================================
# БАЗА ДАННЫХ
# =====================================

def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db():
    with db_lock:
        conn = get_db()

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                video_url TEXT,
                promo_opt_in INTEGER DEFAULT 0,
                next_promo_at TEXT,
                premium_until TEXT,
                paid_premium INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
        """)

        conn.commit()
        conn.close()


def now():
    return datetime.datetime.now()


def date_string(value):
    return value.isoformat(timespec="seconds")


def add_months(date, months=1):
    month_index = date.month - 1 + months
    year = date.year + month_index // 12
    month = month_index % 12 + 1

    day = min(
        date.day,
        calendar.monthrange(year, month)[1]
    )

    return date.replace(
        year=year,
        month=month,
        day=day
    )


def register_user(user_id):
    with db_lock:
        conn = get_db()

        conn.execute("""
            INSERT OR IGNORE INTO users
            (user_id, created_at)
            VALUES (?, ?)
        """, (user_id, date_string(now())))

        conn.commit()
        conn.close()


def save_url(user_id, url):
    register_user(user_id)

    with db_lock:
        conn = get_db()

        conn.execute("""
            UPDATE users
            SET video_url=?
            WHERE user_id=?
        """, (url, user_id))

        conn.commit()
        conn.close()


def get_user(user_id):
    with db_lock:
        conn = get_db()

        row = conn.execute("""
            SELECT video_url,
                   promo_opt_in,
                   next_promo_at,
                   premium_until,
                   paid_premium
            FROM users
            WHERE user_id=?
        """, (user_id,)).fetchone()

        conn.close()

    return row


def is_premium(user_id):
    user = get_user(user_id)

    if not user:
        return False

    if user[4]:
        return True

    if user[3]:
        until = datetime.datetime.fromisoformat(user[3])
        return until > now()

    return False


# =====================================
# ПРОВЕРКА ССЫЛОК
# =====================================

def extract_supported_url(text):
    if not text:
        return None

    text_lower = text.lower()

    if not any(
        domain in text_lower
        for domain in SUPPORTED_PLATFORMS
    ):
        return None

    urls = re.findall(
        r"https?://[^\s<>]+",
        text,
        flags=re.IGNORECASE
    )

    for url in urls:
        url = url.rstrip(".,!?;:)]}\"'")

        lower_url = url.lower()

        if any(
            domain in lower_url
            for domain in SUPPORTED_PLATFORMS
        ):
            return url

    return None


# =====================================
# КЛАВИАТУРЫ
# =====================================

def main_keyboard():
    keyboard = types.InlineKeyboardMarkup()

    keyboard.add(
        types.InlineKeyboardButton(
            "Бесплатно: 720p/30fps",
            callback_data="free"
        )
    )

    keyboard.add(
        types.InlineKeyboardButton(
            "Премиум (5 руб.) -> ОПЛАТИТЬ",
            callback_data="premium"
        )
    )

    return keyboard


def promo_keyboard():
    keyboard = types.InlineKeyboardMarkup()

    keyboard.row(
        types.InlineKeyboardButton(
            "УЧАСТВОВАТЬ",
            callback_data="promo_yes"
        ),
        types.InlineKeyboardButton(
            "НЕ УЧАСТВОВАТЬ",
            callback_data="promo_no"
        )
    )

    return keyboard


def quality_keyboard():
    keyboard = types.InlineKeyboardMarkup()

    for quality in [720, 1080, 1440, 2160]:
        keyboard.add(
            types.InlineKeyboardButton(
                f"{quality}p",
                callback_data=f"quality_{quality}"
            )
        )

    return keyboard


# =====================================
# ПРИВЕТСТВИЕ
# =====================================

@bot.message_handler(commands=["start"])
def start(message):
    register_user(message.from_user.id)

    bot.send_message(
        message.chat.id,
        "Привет! Я всеядный бот-загрузчик. "
        "Скинь мне ссылку на видео из ВК, YouTube, "
        "TikTok, Rutube или Likee, и я скачаю его!"
    )


# =====================================
# ПОЛУЧЕНИЕ ССЫЛОК
# =====================================

@bot.message_handler(
    content_types=["text"],
    func=lambda message: (
        extract_supported_url(message.text) is not None
    )
)
def receive_url(message):
    user_id = message.from_user.id

    url = extract_supported_url(message.text)

    if not url:
        return

    save_url(user_id, url)

    bot.send_message(
        message.chat.id,
        "Ссылка принята!\n\n"
        "В каком качестве вы хотите видео?\n\n"
        "Бесплатно: 720p/30fps/водяной знак\n"
        "Премиум: 1080p, 2к, 4к/60fps\n"
        "Премиум стоит 5 руб.",
        reply_markup=main_keyboard()
    )


# =====================================
# СКАЧИВАНИЕ ВИДЕО
# =====================================

def run_download(user_id, chat_id, quality, premium):
    path = None

    try:
        user = get_user(user_id)

        if not user or not user[0]:
            bot.send_message(
                chat_id,
                "Сначала отправь ссылку на видео."
            )
            return

        url = user[0]

        bot.send_message(
            chat_id,
            "Начинаю скачивание видео..."
        )

        with download_lock:
            path = download_video(
                url=url,
                user_id=user_id,
                quality=quality,
                premium=premium
            )

        if not path or not os.path.isfile(path):
            raise RuntimeError(
                "Не удалось создать видеофайл."
            )

        if os.path.getsize(path) > 49 * 1024 * 1024:
            bot.send_message(
                chat_id,
                "Видео слишком большое для отправки Telegram."
            )
            return

        with open(path, "rb") as video:
            bot.send_video(
                chat_id,
                video,
                supports_streaming=True,
                timeout=300
            )

        bot.send_message(
            chat_id,
            "Кстати, каждый месяц действует акция: "
            "премиум бесплатно на неделю!",
            reply_markup=promo_keyboard()
        )

    except Exception as error:
        print("Ошибка загрузки:", error)

        bot.send_message(
            chat_id,
            "Ошибка скачивания видео:\n"
            + str(error)[:500]
        )

    finally:
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass


# =====================================
# ОБРАБОТКА КНОПОК
# =====================================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    data = call.data

    register_user(user_id)

    # -----------------------------
    # БЕСПЛАТНОЕ СКАЧИВАНИЕ
    # -----------------------------

    if data == "free":

        user = get_user(user_id)

        if not user or not user[0]:
            bot.answer_callback_query(
                call.id,
                "Сначала отправь ссылку!"
            )
            return

        bot.answer_callback_query(
            call.id,
            "Скачивание началось!"
        )

        threading.Thread(
            target=run_download,
            args=(user_id, chat_id, 720, False),
            daemon=True
        ).start()

    # -----------------------------
    # ПРЕМИУМ
    # -----------------------------

    elif data == "premium":

        user = get_user(user_id)

        if not user or not user[0]:
            bot.answer_callback_query(
                call.id,
                "Сначала отправь ссылку!"
            )
            return

        if is_premium(user_id):
            bot.send_message(
                chat_id,
                "Премиум активен! Выбери качество:",
                reply_markup=quality_keyboard()
            )
            bot.answer_callback_query(call.id)
            return

        keyboard = types.InlineKeyboardMarkup()

        keyboard.add(
            types.InlineKeyboardButton(
                "Я оплатил ✅",
                callback_data="payment_done"
            )
        )

        bot.send_message(
            chat_id,
            "Для оплаты Премиума переведите 5 рублей "
            "на кошелек ЮMoney:\n\n"
            f"{YOOMONEY_WALLET}\n\n"
            "После перевода нажмите кнопку «Я оплатил».",
            reply_markup=keyboard
        )

        bot.answer_callback_query(call.id)

    # -----------------------------
    # ЗАЯВКА НА ОПЛАТУ
    # -----------------------------

    elif data == "payment_done":

        with db_lock:
            conn = get_db()

            cursor = conn.execute("""
                INSERT INTO payments
                (user_id, status, created_at)
                VALUES (?, 'pending', ?)
            """, (
                user_id,
                date_string(now())
            ))

            payment_id = cursor.lastrowid

            conn.commit()
            conn.close()

        bot.answer_callback_query(
            call.id,
            "Заявка отправлена!"
        )

        bot.send_message(
            chat_id,
            "Оплата проверяется администратором. "
            "Премиум активируется после подтверждения."
        )

        if ADMIN_ID:

            keyboard = types.InlineKeyboardMarkup()

            keyboard.row(
                types.InlineKeyboardButton(
                    "Подтвердить оплату",
                    callback_data=f"approve_{payment_id}"
                ),
                types.InlineKeyboardButton(
                    "Отклонить",
                    callback_data=f"reject_{payment_id}"
                )
            )

            bot.send_message(
                ADMIN_ID,
                f"Новая заявка на оплату!\n\n"
                f"Пользователь: {user_id}\n"
                f"Сумма: {PRICE} рублей\n"
                f"Заявка: {payment_id}",
                reply_markup=keyboard
            )

    # -----------------------------
    # УЧАСТИЕ В АКЦИИ
    # -----------------------------

    elif data == "promo_yes":

        current = now()
        next_date = add_months(current)

        with db_lock:
            conn = get_db()

            conn.execute("""
                UPDATE users
                SET promo_opt_in=1,
                    next_promo_at=?
                WHERE user_id=?
            """, (
                date_string(next_date),
                user_id
            ))

            conn.commit()
            conn.close()

        bot.answer_callback_query(call.id)

        bot.send_message(
            chat_id,
            "Через месяц премиум будет "
            "в бесплатном доступе на неделю!"
        )

    # -----------------------------
    # ОТКАЗ ОТ АКЦИИ
    # -----------------------------

    elif data == "promo_no":

        with db_lock:
            conn = get_db()

            conn.execute("""
                UPDATE users
                SET promo_opt_in=0,
                    next_promo_at=NULL
                WHERE user_id=?
            """, (user_id,))

            conn.commit()
            conn.close()

        bot.answer_callback_query(
            call.id,
            "Ты не участвуешь в акции."
        )

        bot.send_message(
            chat_id,
            "Хорошо! Ты отказался от участия в акции."
        )

    # -----------------------------
    # ВЫБОР КАЧЕСТВА
    # -----------------------------

    elif data.startswith("quality_"):

        try:
            quality = int(data.split("_")[1])
        except (ValueError, IndexError):
            bot.answer_callback_query(
                call.id,
                "Неверное качество!"
            )
            return

        if quality not in [720, 1080, 1440, 2160]:
            bot.answer_callback_query(
                call.id,
                "Недопустимое качество!"
            )
            return

        if not is_premium(user_id):
            bot.answer_callback_query(
                call.id,
                "Премиум не активен!"
            )
            return

        user = get_user(user_id)

        if not user or not user[0]:
            bot.answer_callback_query(
                call.id,
                "Сначала отправь ссылку!"
            )
            return

        bot.answer_callback_query(
            call.id,
            "Начинаю скачивание!"
        )

        threading.Thread(
            target=run_download,
            args=(user_id, chat_id, quality, True),
            daemon=True
        ).start()

    # -----------------------------
    # ПОДТВЕРЖДЕНИЕ ОПЛАТЫ
    # -----------------------------

    elif data.startswith("approve_") or data.startswith("reject_"):

        if user_id != ADMIN_ID or ADMIN_ID == 0:
            bot.answer_callback_query(
                call.id,
                "Нет доступа!"
            )
            return

        action, payment_id = data.split("_", 1)

        try:
            payment_id = int(payment_id)
        except ValueError:
            bot.answer_callback_query(
                call.id,
                "Неверный номер заявки!"
            )
            return

        with db_lock:
            conn = get_db()

            payment = conn.execute("""
                SELECT user_id, status
                FROM payments
                WHERE id=?
            """, (payment_id,)).fetchone()

            if not payment or payment[1] != "pending":
                conn.close()

                bot.answer_callback_query(
                    call.id,
                    "Заявка уже обработана!"
                )
                return

            target_user = payment[0]

            if action == "approve":

                conn.execute("""
                    UPDATE payments
                    SET status='approved'
                    WHERE id=?
                """, (payment_id,))

                conn.execute("""
                    UPDATE users
                    SET paid_premium=1
                    WHERE user_id=?
                """, (target_user,))

            else:

                conn.execute("""
                    UPDATE payments
                    SET status='rejected'
                    WHERE id=?
                """, (payment_id,))

            conn.commit()
            conn.close()

        if action == "approve":

            bot.send_message(
                target_user,
                "Оплата подтверждена! "
                "Премиум активирован.",
                reply_markup=quality_keyboard()
            )

            bot.answer_callback_query(
                call.id,
                "Оплата подтверждена!"
            )

        else:

            bot.send_message(
                target_user,
                "Оплата не подтверждена. "
                "Обратись к администратору."
            )

            bot.answer_callback_query(
                call.id,
                "Заявка отклонена."
            )


# =====================================
# АВТОМАТИЧЕСКАЯ ЕЖЕМЕСЯЧНАЯ АКЦИЯ
# =====================================

def promo_worker():

    while True:

        try:
            current = now()

            with db_lock:
                conn = get_db()

                users = conn.execute("""
                    SELECT user_id,
                           next_promo_at,
                           premium_until
                    FROM users
                    WHERE promo_opt_in=1
                """).fetchall()

                conn.close()

            for user_id, next_promo, premium_until in users:

                # -------------------------
                # ЗАВЕРШЕНИЕ 7 ДНЕЙ
                # -------------------------

                if premium_until:

                    until = datetime.datetime.fromisoformat(
                        premium_until
                    )

                    if current >= until:

                        next_date = add_months(until)

                        with db_lock:
                            conn = get_db()

                            conn.execute("""
                                UPDATE users
                                SET premium_until=NULL,
                                    next_promo_at=?
                                WHERE user_id=?
                                  AND premium_until=?
                            """, (
                                date_string(next_date),
                                user_id,
                                premium_until
                            ))

                            conn.commit()
                            conn.close()

                        try:
                            bot.send_message(
                                user_id,
                                "Неделя премиума прошла! "
                                "Через месяц премиум снова будет "
                                "в бесплатном доступе!"
                            )
                        except Exception as error:
                            print(
                                "Не удалось отправить уведомление:",
                                error
                            )

                    continue

                # -------------------------
                # НАЧАЛО НОВОГО ПРЕМИУМА
                # -------------------------

                if next_promo:

                    start_date = datetime.datetime.fromisoformat(
                        next_promo
                    )

                    if current >= start_date:

                        premium_end = start_date + datetime.timedelta(
                            days=7
                        )

                        with db_lock:
                            conn = get_db()

                            cursor = conn.execute("""
                                UPDATE users
                                SET premium_until=?
                                WHERE user_id=?
                                  AND premium_until IS NULL
                                  AND next_promo_at=?
                            """, (
                                date_string(premium_end),
                                user_id,
                                next_promo
                            ))

                            conn.commit()
                            activated = cursor.rowcount > 0
                            conn.close()

                        if activated:
                            try:
                                bot.send_message(
                                    user_id,
                                    "Бесплатный премиум начался!",
                                    reply_markup=quality_keyboard()
                                )
                            except Exception as error:
                                print(
                                    "Ошибка уведомления:",
                                    error
                                )

        except Exception as error:
            print(
                "Ошибка автоматической акции:",
                error
            )

        # Проверка каждую минуту
        time.sleep(60)


# =====================================
# ЗАПУСК БОТА
# =====================================

if __name__ == "__main__":

    init_db()

    worker = threading.Thread(
        target=promo_worker,
        daemon=True
    )

    worker.start()

    print("VK_VIDIO_DOWNLOAD_bot запущен!")
    print("Поддерживаются ВК, YouTube, TikTok, Rutube и Likee.")

    bot.infinity_polling(timeout=90, long_polling_timeout=90)

