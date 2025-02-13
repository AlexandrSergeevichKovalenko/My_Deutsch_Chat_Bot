import os
import logging
import openai
import psycopg2
import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler

# === Настройки бота ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# 🔹 Если переменная окружения пуста, используем токен вручную (только временно)
if not TELEGRAM_BOT_TOKEN:
    TELEGRAM_BOT_TOKEN = "7183316017:AAHXBtqC0nvGhpgwJwhfDId1TUt0aR3JFww"

# 🔹 Отладочный вывод, чтобы проверить, какой токен получен
print(f"DEBUG: TELEGRAM_BOT_TOKEN = {repr(TELEGRAM_BOT_TOKEN)}")

# 🔹 Проверяем, действительно ли переменная пуста
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ Ошибка: TELEGRAM_BOT_TOKEN не задан. Проверь переменные окружения!")

GROUP_CHAT_ID = -1002347376305  # ID вашей группы

print("🚀 Все переменные окружения Railway:")
for key, value in os.environ.items():
    print(f"{key}: {value[:10]}...")  # Выводим первые 10 символов для безопасности

# === Настройка OpenAI API ===
openai.api_key = os.getenv("OPENAI_API_KEY")

# 🔍 Debugging: Проверяем, что Railway видит переменную
print(f"DEBUG: OPENAI_API_KEY = {repr(openai.api_key)}")

if not openai.api_key:
    raise ValueError("❌ Ошибка: OPENAI_API_KEY не задан. Проверь переменные окружения!")

# === Подключение к базе данных PostgreSQL ===
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("❌ Ошибка: DATABASE_URL не задан. Проверь переменные окружения!")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# Проверка подключения
conn = get_db_connection()
cursor = conn.cursor()
cursor.execute("SELECT version();")
db_version = cursor.fetchone()
print(f"✅ База данных подключена! Версия: {db_version}")
cursor.close()
conn.close()

def initialize_database():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Таблица с оригинальными предложениями
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sentences (
            id SERIAL PRIMARY KEY,
            sentence TEXT NOT NULL
        );
    """)

    # ✅ Таблица для переводов пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS translations (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            username TEXT,
            sentence_id INT NOT NULL,
            user_translation TEXT NOT NULL,
            score INT,
            feedback TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ✅ Новая таблица для всех сообщений пользователей (чтобы учитывать ленивых)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            username TEXT NOT NULL,
            message TEXT NOT NULL,
            timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
    """)


    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_sentences (
            id SERIAL PRIMARY KEY,
            date DATE NOT NULL DEFAULT CURRENT_DATE,
            sentence TEXT NOT NULL,
            unique_id INT NOT NULL
        );
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_progress (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            completed BOOLEAN DEFAULT FALSE
        );
    """)


    conn.commit()
    cursor.close()
    conn.close()
    print("✅ Таблицы sentences, translations и messages проверены и готовы к использованию.")




# Вызываем при старте бота
initialize_database()


# === Логирование ===
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)


async def log_message(update: Update, context: CallbackContext):
    """Логирует все сообщения в базе данных"""
    
    if not update.message:  # Если update.message = None, просто игнорируем
        return  

    user = update.message.from_user
    message_text = update.message.text.strip()

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO messages (user_id, username, message) VALUES (%s, %s, %s);",
        (user.id, user.username or user.first_name, message_text)
    )

    conn.commit()
    cursor.close()
    conn.close()


async def send_morning_reminder(context: CallbackContext):
    message = (
        "🌅 **Доброе утро, переводчики!**\n\n"
        "Чтобы принять участие в переводе, напишите команду `/letsgo`. После этого вам будут высланы предложения.\n\n"
        "📌 **Важно:**\n"
        "🔹 Переводите максимально точно и быстро — время влияет на итоговую оценку!\n"
        "🔹 После перевода всех предложений обязательно выполните `/done`.\n"
        "🔹 В 09:00, 12:00 и 15:00 будут **промежуточные итоги** по каждому участнику.\n"
        "🔹 Итоговые результаты дня отправляются в 22:00."
    )
    
    # 📌 Список команд
    commands = (
        "📜 **Доступные команды:**\n"
        "/letsgo - Получить задания на перевод\n"
        "/done - Завершить перевод (фиксирует время)\n"
        "/translate - Отправить переводы\n"
        "/getmore - Получить дополнительные предложения\n"
        "/stats - Узнать свою статистику\n"
    )
    
    # Отправляем два отдельных сообщения
    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=message)
    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=commands)




async def letsgo(update: Update, context: CallbackContext):
    user = update.message.from_user
    user_id = user.id
    username = user.username or user.first_name

    conn = get_db_connection()
    cursor = conn.cursor()

    # Проверяем, не запустил ли уже пользователь перевод
    cursor.execute("SELECT start_time FROM user_progress WHERE user_id = %s;", (user_id,))
    row = cursor.fetchone()

    if row:
        logging.info(f"⏳ Пользователь {username} ({user_id}) уже начал перевод.")
        await update.message.reply_text("❌ Вы уже начали перевод! Завершите его перед повторным запуском.")
        cursor.close()
        conn.close()
        return

    # Фиксируем время старта
    cursor.execute(
        "INSERT INTO user_progress (user_id, username, start_time) VALUES (%s, %s, NOW()) "
        "ON CONFLICT (user_id) DO UPDATE SET start_time = NOW(), completed = FALSE;",
        (user_id, username)
    )
    conn.commit()
    cursor.close()
    conn.close()

    logging.info(f"🚀 Пользователь {username} ({user_id}) начал перевод. Время зафиксировано.")

    # Отправляем пользователю задания
    sentences = await get_original_sentences()
    tasks = "\n".join([f"{i+1}. {sentence}" for i, sentence in enumerate(sentences)])

    await update.message.reply_text(
        f"🚀 **Вы начали перевод! Время пошло.**\n\nВот ваши предложения:\n{tasks}\n\n"
        "Отправьте все переводы и завершите с помощью `/done`."
    )


# async def done(update: Update, context: CallbackContext):
#     user = update.message.from_user
#     user_id = user.id

#     conn = get_db_connection()
#     cursor = conn.cursor()

#     # Проверяем, начинал ли пользователь перевод
#     cursor.execute("SELECT start_time FROM user_progress WHERE user_id = %s AND completed = FALSE;", (user_id,))
#     row = cursor.fetchone()

#     if not row:
#         await update.message.reply_text("❌ Вы еще не начинали перевод! Используйте /letsgo.")
#         cursor.close()
#         conn.close()
#         return

#     # Фиксируем время завершения
#     cursor.execute(
#         "UPDATE user_progress SET end_time = NOW(), completed = TRUE WHERE user_id = %s;",
#         (user_id,)
#     )
#     conn.commit()
#     cursor.close()
#     conn.close()

#     await update.message.reply_text("✅ Перевод завершен! Ваш результат будет оценен.")


async def done(update: Update, context: CallbackContext):
    user = update.message.from_user
    user_id = user.id

    conn = get_db_connection()
    cursor = conn.cursor()

    # Проверяем, есть ли у пользователя активный перевод
    cursor.execute("SELECT start_time FROM user_progress WHERE user_id = %s AND completed = FALSE;", (user_id,))
    row = cursor.fetchone()

    if not row:
        await update.message.reply_text("❌ Вы ещё не начинали перевод! Используйте /letsgo.")
        cursor.close()
        conn.close()
        return

    # Подтверждение перед завершением
    await update.message.reply_text(
        "⚠️ **Вы уверены, что хотите завершить перевод?**\n\n"
        "❗ **Проверьте, все ли переводы отправлены!**\n"
        "Если вы уверены, отправьте команду **/confirm_done**."
    )

    cursor.close()
    conn.close()


async def confirm_done(update: Update, context: CallbackContext):
    user = update.message.from_user
    user_id = user.id

    conn = get_db_connection()
    cursor = conn.cursor()

    # Проверяем, есть ли активный перевод
    cursor.execute(
        "SELECT start_time FROM user_progress WHERE user_id = %s AND completed = FALSE;",
        (user_id,)
    )
    row = cursor.fetchone()

    if not row:
        await update.message.reply_text("❌ Ошибка: у вас нет активного перевода!")
        cursor.close()
        conn.close()
        return

    # Фиксируем `end_time`
    cursor.execute(
        "UPDATE user_progress SET end_time = NOW(), completed = TRUE WHERE user_id = %s;",
        (user_id,)
    )

    # Подсчитываем количество переведённых предложений
    cursor.execute(
        "SELECT COUNT(*) FROM translations WHERE user_id = %s AND timestamp::date = CURRENT_DATE;",
        (user_id,)
    )
    translated_count = cursor.fetchone()[0]

    # Получаем общее количество выданных предложений
    cursor.execute(
        "SELECT COUNT(*) FROM daily_sentences WHERE date = CURRENT_DATE;"
    )
    total_sentences = cursor.fetchone()[0]

    # Считаем штраф за пропущенные предложения (-10 баллов за каждое)
    missing_translations = total_sentences - translated_count
    penalty = missing_translations * 10 if missing_translations > 0 else 0

    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text(
        f"✅ **Перевод завершён!**\n\n"
        f"📜 **Вы перевели:** {translated_count}/{total_sentences} предложений.\n"
        f"🚨 **Штраф за пропущенные:** -{penalty} баллов.\n"
        f"🏆 Итог будет учтён в вечернем рейтинге!"
    )









# === Функция для генерации новых предложений с помощью GPT-4 ===
import asyncio

async def generate_sentences():
    client = openai.AsyncOpenAI(api_key=openai.api_key)
    prompt = """
    Придумай 7 предложений уровня B2-C1 на **русском языке** для перевода на **немецкий**.
    
    **Требования:**
    - Используй **пассивный залог** и **Konjunktiv II** хотя бы в половине предложений.
    - Каждое предложение должно быть **на отдельной строке**.
    - **НЕ добавляй перевод!** Только оригинальные русские предложения.

    **Пример формата вывода:**
    Этот город был основан более 300 лет назад.
    Если бы у нас было больше времени, мы бы посетили все музеи.
    Важно, чтобы все решения были приняты коллегиально.
    Книга была написана известным писателем в прошлом веке.
    Было бы лучше, если бы он согласился на это предложение.
    Нам сказали, что проект будет завершен через неделю.
    Если бы он мог говорить на немецком, он бы легко нашел работу.
    """

    for attempt in range(5):  # Попробовать до 5 раз
        try:
            response = await client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[{"role": "user", "content": prompt}]
            )
            return response.choices[0].message.content.split("\n")
        except openai.RateLimitError:
            wait_time = (attempt + 1) * 2  # Увеличиваем задержку: 2, 4, 6 сек
            print(f"⚠️ OpenAI API Rate Limit. Ждем {wait_time} сек...")
            await asyncio.sleep(wait_time)

    print("❌ Ошибка: не удалось получить ответ от OpenAI. Используем запасные предложения.")
    return ["Запасное предложение 1", "Запасное предложение 2"]



async def get_original_sentences():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT sentence FROM sentences ORDER BY RANDOM() LIMIT 5;")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if rows:
        return [row[0] for row in rows]
    else:
        print("⚠️ В базе данных нет предложений, генерируем новые через GPT-4...")
        return await generate_sentences()

# === Команда для админа: задать свои предложения ===
# async def set_new_tasks(update: Update, context: CallbackContext):
#     if update.message.chat.id != GROUP_CHAT_ID:
#         return
    
#     new_tasks = update.message.text.replace("/newtasks", "").strip().split("\n")
#     if len(new_tasks) < 3:
#         await update.message.reply_text("❌ Ошибка: Введите минимум 3 предложения через новую строку.")
#         return
    
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     cursor.execute("DELETE FROM sentences;")
#     for task in new_tasks:
#         cursor.execute("INSERT INTO sentences (sentence) VALUES (%s);", (task,))
#     conn.commit()
#     cursor.close()
#     conn.close()
    
#     await update.message.reply_text("✅ Новые задания сохранены!")
# Бот принимает предложения только в личке От админа группы
async def set_new_tasks(update: Update, context: CallbackContext):
    user = update.message.from_user
    chat_id = update.message.chat.id

    # ✅ Проверяем, чтобы команда работала ТОЛЬКО в личных сообщениях
    if chat_id != user.id:
        await update.message.reply_text("❌ Напишите эту команду мне в ЛИЧНЫЕ сообщения.")
        return

    new_tasks = update.message.text.replace("/newtasks", "").strip().split("\n")
    
    if len(new_tasks) < 3:
        await update.message.reply_text("❌ Ошибка: Введите минимум 3 предложения через новую строку.")
        return

    # ✅ Сохраняем новые предложения в БД
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sentences;")
    for task in new_tasks:
        cursor.execute("INSERT INTO sentences (sentence) VALUES (%s);", (task,))
    conn.commit()
    cursor.close()
    conn.close()
    
    await update.message.reply_text("✅ Новые задания сохранены! Они появятся в группе завтра утром.")


# === Рассылка предложений в 08:00 ===
application = None  # Глобальная переменная для хранения объекта бота

# async def send_morning_tasks(context=None):
#     sentences = await get_original_sentences()
#     tasks = "\n".join([f"{i+1}. {sentence}" for i, sentence in enumerate(sentences)])
#     message = f"🌅 **Guten Morgen, малые! Ловите подачу:**\n\n{tasks}\n\n Xватит чесать жопу. Переводите Предложения. Формат ответа: /translate Номер предложения Перевод"

#     # Проверяем, передан ли context, если нет — берем бота из application
#     if context:
#         bot = context.bot
#     else:
#         bot = application.bot  # Берем бота напрямую, если контекста нет

#     await bot.send_message(chat_id=GROUP_CHAT_ID, text=message)

# async def send_more_tasks(update: Update, context: CallbackContext):
#     conn = get_db_connection()
#     cursor = conn.cursor()

#     # 🔹 Find out how many sentences have already been sent today
#     cursor.execute("SELECT COUNT(*) FROM daily_sentences WHERE date = CURRENT_DATE;")
#     start_index = cursor.fetchone()[0]  # Number of already sent sentences

#     # 🔹 Generate new sentences
#     sentences = await get_original_sentences()
#     tasks = []

#     for i, sentence in enumerate(sentences, start=start_index + 1):  # Continue numbering
#         tasks.append(f"{i}. {sentence}")
#         cursor.execute(
#             "INSERT INTO daily_sentences (date, sentence, unique_id) VALUES (CURRENT_DATE, %s, %s);",
#             (sentence, i),
#         )

#     conn.commit()
#     cursor.close()
#     conn.close()

#     # 🔹 Format the message
#     message = f"🔹 **More Sentences Added!**\n\n" + "\n".join(tasks) + \
#               "\n\nFormat your reply: `/translate <number> <your translation>`"

#     await update.message.reply_text(message)



async def send_more_tasks(update: Update, context: CallbackContext):
    user = update.message.from_user
    user_id = user.id
    username = user.username or user.first_name

    conn = get_db_connection()
    cursor = conn.cursor()

    # 🔹 Проверяем, начинал ли пользователь перевод
    cursor.execute("SELECT start_time FROM user_progress WHERE user_id = %s;", (user_id,))
    row = cursor.fetchone()

    if not row:
        await update.message.reply_text("❌ Вы ещё не начинали перевод! Используйте /letsgo.")
        cursor.close()
        conn.close()
        return

    # 🔹 Фиксируем **новое время старта** (но НЕ сбрасываем старое!)
    cursor.execute(
        """
        INSERT INTO user_progress (user_id, username, start_time, completed)
        VALUES (%s, %s, NOW(), FALSE)
        ON CONFLICT (user_id) DO UPDATE SET start_time = NOW(), completed = FALSE;
        """,
        (user_id, username)
    )

    # 🔹 Узнаём, сколько предложений уже было отправлено сегодня
    cursor.execute("SELECT COUNT(*) FROM daily_sentences WHERE date = CURRENT_DATE;")
    start_index = cursor.fetchone()[0]  # Количество уже отправленных предложений

    # 🔹 Генерируем новые предложения
    sentences = await get_original_sentences()
    tasks = []

    for i, sentence in enumerate(sentences, start=start_index + 1):  # Нумерация продолжается
        tasks.append(f"{i}. {sentence}")
        cursor.execute(
            "INSERT INTO daily_sentences (date, sentence, unique_id) VALUES (CURRENT_DATE, %s, %s);",
            (sentence, i),
        )

    conn.commit()
    cursor.close()
    conn.close()

    # 🔹 Отправляем пользователю новые предложения
    message = (
        f"✅ **Вы запросили дополнительные предложения! Время пошло.**\n\n"
        + "\n".join(tasks) +
        "\n\n📌 Формат ответа: `/translate <номер> <ваш перевод>`\n"
        "⚠ **Не забудьте завершить перевод с помощью** `/done`!"
    )

    await update.message.reply_text(message)





# === GPT-4 Функция для оценки перевода ===

import asyncio

async def check_translation(original_text, user_translation):
    client = openai.AsyncOpenAI(api_key=openai.api_key)  # Новый API-клиент
    
    prompt = f"""
    Ты профессиональный лингвист и преподаватель немецкого языка.
    Твоя задача — проверить перевод с **русского** на **немецкий**.

    - Оригинальный текст (на русском): "{original_text}"
    - Перевод пользователя (на немецком): "{user_translation}"

    **Требования к проверке**:
    1. **Выставь оценку от 0 до 100** (по уровню точности, грамматики и стиля, Соответствие содержанию, При полном несоответствии содержанию оценка ноль).
    2. ** Детально объясни ошибки**, если они есть (не более 2-3 предложений). Обязательно объясни грамматическую конструкцию И как она строится должна правильно.
    3. **Дай рекомендацию**, как улучшить перевод(В случае если перевод не верен). Обязательно укажи правильный вариант перевода(это должен быть наиболее часто встречаемый перевод).
    Можешь также указать список слов-синонимов, которые можно было бы использовать в данном переводе(только для слов уровня B2 И C1).

    **Формат ответа (без лишних символов, только текст!)**:
    Оценка: X/100
    Ошибки: ...
    Рекомендация: ...
    """

    for attempt in range(3):  # До 3-х попыток при ошибках API
        try:
            response = await client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[{"role": "user", "content": prompt}]
            )
            return response.choices[0].message.content.strip()  # Убираем лишние пробелы
        except openai.RateLimitError:
            wait_time = (attempt + 1) * 5  # 5, 10, 15 секунд
            print(f"⚠️ OpenAI API перегружен. Ждём {wait_time} сек...")
            await asyncio.sleep(wait_time)

    return "❌ Ошибка: Не удалось получить оценку. Попробуйте позже."

import re

# async def check_user_translation(update: Update, context: CallbackContext):
#     user_id = update.message.from_user.id  # Получаем ID отправителя
#     message_text = update.message.text

#     # Проверяем, соответствует ли сообщение формату "/перевод <номер> <текст>"
#     match = re.match(r"/перевод (\d+) (.+)", message_text)
#     if not match:
#         await update.message.reply_text("❌ Ошибка: Используйте формат `/перевод <номер> <ваш перевод>`")
#         return

#     sentence_number = int(match.group(1))  # Номер предложения
#     user_translation = match.group(2)  # Сам перевод

#     # Получаем оригинальные предложения
#     original_sentences = await get_original_sentences()

#     # Проверяем, существует ли предложение с таким номером
#     if sentence_number < 1 or sentence_number > len(original_sentences):
#         await update.message.reply_text(f"❌ Ошибка: Введите номер от 1 до {len(original_sentences)}.")
#         return

#     original_text = original_sentences[sentence_number - 1]  # Оригинал на русском

#     # Проверяем перевод через GPT
#     feedback = await check_translation(original_text, user_translation)

#     # Отправляем пользователю оценку
#     await update.message.reply_text(
#         f"👤 {update.message.from_user.first_name}, ваш перевод для {sentence_number}-го предложения:\n"
#         f"✅ Оценка: {feedback}"
#     )

import re
import logging

# async def check_user_translation(update: Update, context: CallbackContext):
#     message_text = update.message.text.strip()  # Убираем лишние пробелы
#     logging.info(f"📥 Получена команда: {message_text}")  # ✅ Логируем команду

#     # Поддержка и "/перевод", и "/translate"
#     match = re.match(r"^/(перевод|translate)\s+(\d+)\s+(.+)$", message_text)

#     if not match:
#         logging.info(f"⚠️ Команда не распознана: {message_text}")  # ✅ Лог ошибки
#         await update.message.reply_text("❌ Ошибка: Используйте формат `/перевод <номер> <ваш перевод>`")
#         return

#     sentence_number = int(match.group(2))  # ✅ Исправлено: Берём номер из второй группы
#     user_translation = match.group(3).strip()  # ✅ Исправлено: Берём перевод из третьей группы

#     logging.info(f"✅ Распознано: Номер={sentence_number}, Перевод={user_translation}")

#     # Получаем список предложений
#     original_sentences = await get_original_sentences()

#     # Проверяем, существует ли такое предложение
#     if sentence_number < 1 or sentence_number > len(original_sentences):
#         await update.message.reply_text(f"❌ Ошибка: Введите номер от 1 до {len(original_sentences)}.")
#         return

#     original_text = original_sentences[sentence_number - 1]  # ✅ Берём нужное предложение

#     # Проверяем перевод через GPT
#     feedback = await check_translation(original_text, user_translation)

#     # Отправляем пользователю оценку
#     await update.message.reply_text(
#         f"👤 {update.message.from_user.first_name}, ваш перевод для {sentence_number}-го предложения:\n"
#         f"✅ Оценка: {feedback}"
#     )

# async def check_user_translation(update: Update, context: CallbackContext):
#     message_text = update.message.text.strip()
#     logging.info(f"📥 Получена команда: {message_text}")

#     match = re.match(r"^/(перевод|translate)\s+(\d+)\s+(.+)$", message_text)
#     if not match:
#         logging.info(f"⚠️ Команда не распознана: {message_text}")
#         await update.message.reply_text("❌ Ошибка: Используйте формат `/перевод <номер> <ваш перевод>`")
#         return

#     sentence_number = int(match.group(2))
#     user_translation = match.group(3).strip()
#     user_id = update.message.from_user.id
#     username = update.message.from_user.first_name

#     logging.info(f"✅ Распознано: Номер={sentence_number}, Перевод={user_translation}")

#     original_sentences = await get_original_sentences()

#     if sentence_number < 1 or sentence_number > len(original_sentences):
#         await update.message.reply_text(f"❌ Ошибка: Введите номер от 1 до {len(original_sentences)}.")
#         return

#     original_text = original_sentences[sentence_number - 1]

#     # Проверяем перевод через GPT
#     # Log the original sentence before checking translation
#     logging.info(f"📌 Проверка перевода. Оригинальное предложение: {original_text}")

#     # Проверяем перевод через GPT
#     feedback = await check_translation(original_text, user_translation)


#     # Получаем оценку (из строки вида "Оценка: 85/100")
#     score_match = re.search(r"Оценка:\s*(\d+)/100", feedback)
#     score = int(score_match.group(1)) if score_match else None

#     # Записываем в базу данных
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     cursor.execute(
#         """
#         INSERT INTO translations (user_id, username, sentence_id, user_translation, score, feedback)
#         VALUES (%s, %s, %s, %s, %s, %s);
#         """,
#         (user_id, username, sentence_number, user_translation, score, feedback),
#     )
#     conn.commit()
#     cursor.close()
#     conn.close()

#     # Отправляем пользователю оценку
#     await update.message.reply_text(
#         f"👤 {username}, ваш перевод для {sentence_number}-го предложения:\n"
#         f"✅ Оценка: {feedback}"
#     )

import re
import logging

import re
import logging

# async def check_user_translation(update: Update, context: CallbackContext):
#     if not update.message or not update.message.text:
#         return  

#     message_text = update.message.text.strip()
#     logging.info(f"📥 Получена команда: {message_text}")

#     # Удаляем команду `/translate`
#     translations_text = message_text.replace("/translate", "").strip()
    
#     if not translations_text:
#         await update.message.reply_text("❌ Ошибка: После /translate должен идти список переводов.")
#         return

#     # Разбираем входной текст на номера предложений и переводы
#     pattern = re.compile(r"(\d+)\.\s+(.+)")
#     translations = pattern.findall(translations_text)

#     if not translations:
#         await update.message.reply_text("❌ Ошибка: Используйте формат: \n\n/translate\n1. <перевод>\n2. <перевод>")
#         return

#     user_id = update.message.from_user.id
#     username = update.message.from_user.first_name

#     conn = get_db_connection()
#     cursor = conn.cursor()

#     results = []  # Храним результаты для отправки в Telegram

#     for number_str, user_translation in translations:
#         sentence_number = int(number_str)

#         # Проверяем, есть ли это предложение в базе
#         cursor.execute(
#             "SELECT sentence FROM daily_sentences WHERE date = CURRENT_DATE AND unique_id = %s;",
#             (sentence_number,),
#         )
#         row = cursor.fetchone()

#         if not row:
#             results.append(f"❌ Ошибка: Предложение с номером {sentence_number} не найдено.")
#             continue

#         original_text = row[0]

#         # 🔹 **Проверяем, отправлял ли этот пользователь перевод этого предложения**
#         cursor.execute(
#             "SELECT id FROM translations WHERE user_id = %s AND sentence_id = %s AND timestamp::date = CURRENT_DATE;",
#             (user_id, sentence_number)
#         )
#         existing_translation = cursor.fetchone()

#         if existing_translation:
#             results.append(f"⚠️ Вы уже переводили предложение {sentence_number}. Только первый перевод учитывается!")
#             continue

#         logging.info(f"📌 Проверяем перевод №{sentence_number}: {user_translation}")

#         # Проверяем перевод через GPT
#         feedback = await check_translation(original_text, user_translation)

#         # Получаем оценку из строки "Оценка: 85/100"
#         score_match = re.search(r"Оценка:\s*(\d+)/100", feedback)
#         score = int(score_match.group(1)) if score_match else None

#         # 🔹 **Сохраняем только ПЕРВЫЙ перевод пользователя**
#         cursor.execute(
#             """
#             INSERT INTO translations (user_id, username, sentence_id, user_translation, score, feedback)
#             VALUES (%s, %s, %s, %s, %s, %s);
#             """,
#             (user_id, username, sentence_number, user_translation, score, feedback),
#         )
#         conn.commit()

#         results.append(f"📜 **Предложение {sentence_number}**\n🎯 Оценка: {feedback}")

#     cursor.close()
#     conn.close()

#     # Отправляем пользователю результаты всех переводов
#     if results:
#         await update.message.reply_text("\n\n".join(results))



import re
import logging

async def check_user_translation(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return  

    message_text = update.message.text.strip()
    logging.info(f"📥 Получена команда: {message_text}")

    # Удаляем команду `/translate`
    translations_text = message_text.replace("/translate", "").strip()
    
    if not translations_text:
        await update.message.reply_text("❌ Ошибка: После /translate должен идти список переводов.")
        return

    # Разбираем входной текст на номера предложений и переводы
    pattern = re.compile(r"(\d+)\.\s+(.+)")
    translations = pattern.findall(translations_text)

    if not translations:
        await update.message.reply_text("❌ Ошибка: Используйте формат: \n\n/translate\n1. <перевод>\n2. <перевод>")
        return

    user_id = update.message.from_user.id
    username = update.message.from_user.first_name

    conn = get_db_connection()
    cursor = conn.cursor()

    # 🔹 Получаем предложения, которые были отправлены ЭТОМУ пользователю
    cursor.execute(
        "SELECT unique_id FROM daily_sentences WHERE date = CURRENT_DATE AND unique_id IN "
        "(SELECT sentence_id FROM user_progress WHERE user_id = %s);",
        (user_id,)
    )
    allowed_sentences = {row[0] for row in cursor.fetchall()}  # Собираем в set() для быстрого поиска

    results = []  # Храним результаты для Telegram

    for number_str, user_translation in translations:
        sentence_number = int(number_str)

        # 🔹 **Проверяем, имеет ли пользователь право переводить это предложение**
        if sentence_number not in allowed_sentences:
            results.append(f"❌ Ошибка: Предложение {sentence_number} вам не принадлежит!")
            continue

        # 🔹 **Проверяем, есть ли это предложение в базе**
        cursor.execute(
            "SELECT sentence FROM daily_sentences WHERE date = CURRENT_DATE AND unique_id = %s;",
            (sentence_number,),
        )
        row = cursor.fetchone()

        if not row:
            results.append(f"❌ Ошибка: Предложение с номером {sentence_number} не найдено.")
            continue

        original_text = row[0]

        # 🔹 **Проверяем, отправлял ли этот пользователь перевод этого предложения**
        cursor.execute(
            "SELECT id FROM translations WHERE user_id = %s AND sentence_id = %s AND timestamp::date = CURRENT_DATE;",
            (user_id, sentence_number)
        )
        existing_translation = cursor.fetchone()

        if existing_translation:
            results.append(f"⚠️ Вы уже переводили предложение {sentence_number}. Только первый перевод учитывается!")
            continue

        logging.info(f"📌 Проверяем перевод №{sentence_number}: {user_translation}")

        # Проверяем перевод через GPT
        feedback = await check_translation(original_text, user_translation)

        # Получаем оценку из строки "Оценка: 85/100"
        score_match = re.search(r"Оценка:\s*(\d+)/100", feedback)
        score = int(score_match.group(1)) if score_match else None

        # 🔹 **Сохраняем только ПЕРВЫЙ перевод пользователя**
        cursor.execute(
            """
            INSERT INTO translations (user_id, username, sentence_id, user_translation, score, feedback)
            VALUES (%s, %s, %s, %s, %s, %s);
            """,
            (user_id, username, sentence_number, user_translation, score, feedback),
        )
        conn.commit()

        results.append(f"📜 **Предложение {sentence_number}**\n🎯 Оценка: {feedback}")

    cursor.close()
    conn.close()

    # Отправляем пользователю результаты всех переводов
    if results:
        await update.message.reply_text("\n\n".join(results))









# # === Оценка переводов ===
# async def handle_message(update: Update, context: CallbackContext):
#     user_text = update.message.text
#     original_sentences = await get_original_sentences()
#     best_score = 0
#     best_feedback = ""

#     for original_text in original_sentences:
#         feedback = await check_translation(original_text, user_text)
#         try:
#             score = int(feedback.split("/")[0])
#         except ValueError:
#             score = 50  
#         if score > best_score:
#             best_score = score
#             best_feedback = feedback

#     await update.message.reply_text(f"✅ Оценка: {best_score}/100\n📝 Комментарий: {best_feedback}")

# === Итог дня в 20:00 ===
# async def send_daily_summary(context: CallbackContext):
#     conn = get_db_connection()
#     cursor = conn.cursor()

#     # 1️⃣ Получаем список всех, кто делал переводы
#     cursor.execute("""
#         SELECT DISTINCT user_id, username 
#         FROM translations 
#         WHERE timestamp::date = CURRENT_DATE;
#     """)
#     active_users = {row[0]: row[1] for row in cursor.fetchall()}

#     # 2️⃣ Получаем список всех, кто писал хоть что-то в чат
#     cursor.execute("""
#         SELECT DISTINCT user_id, username
#         FROM messages
#         WHERE timestamp::date = CURRENT_DATE;
#     """)  
#     all_users = {row[0]: row[1] for row in cursor.fetchall()}

#     # 3️⃣ Получаем статистику переводов
#     cursor.execute("""
#         SELECT username, COUNT(*), AVG(score) 
#         FROM translations 
#         WHERE timestamp::date = CURRENT_DATE 
#         GROUP BY username 
#         ORDER BY COUNT(*) DESC;
#     """)
#     rows = cursor.fetchall()

#     cursor.close()
#     conn.close()

#     # Если никто не делал переводов
#     if not rows:
#         await context.bot.send_message(chat_id=GROUP_CHAT_ID, text="📊 Да вы что охуели. Вы же нихуя за сегодня не сделали!")
#         return

#     summary = "📊 **Итоги дня:**\n\n"
    
#     # 4️⃣ Записываем всех, кто делал переводы
#     for username, count, avg_score in rows:
#         summary += f"👤 {username}: **{count} перевод(ов)**, средняя оценка: {avg_score:.1f}/100\n"

#     # 5️⃣ Определяем "ленивых", кто писал в чат, но не переводил
#     lazy_users = {uid: uname for uid, uname in all_users.items() if uid not in active_users}
#     if lazy_users:
#         summary += "\n🚨 **Ленивые мудаки:**\n"
#         for username in lazy_users.values():
#             summary += f"👤 {username}: ленивое дерьмо\n"

#     await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=summary)

async def send_progress_report(context: CallbackContext):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Получаем количество предложений, высланных сегодня
    cursor.execute("SELECT COUNT(*) FROM daily_sentences WHERE date = CURRENT_DATE;")
    total_sentences = cursor.fetchone()[0]

    if total_sentences == 0:
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text="⚠️ Сегодня ещё нет заданий.")
        return

    # Получаем статистику по пользователям
    cursor.execute("""
        SELECT user_progress.username, COUNT(translations.id) AS переведено
        FROM user_progress
        LEFT JOIN translations ON user_progress.user_id = translations.user_id
        WHERE translations.timestamp::date = CURRENT_DATE
        GROUP BY user_progress.username;
    """)
    rows = cursor.fetchall()
    
    cursor.close()
    conn.close()

    # Формируем отчёт
    progress_report = "📊 **Промежуточные итоги перевода:**\n\n"

    for username, translated_count in rows:
        percent = (translated_count / total_sentences) * 100
        progress_report += f"👤 {username}: {translated_count}/{total_sentences} ({percent:.1f}%)\n"

    # Проверяем, кто вообще не стартанул
    cursor.execute("SELECT username FROM user_progress WHERE completed = FALSE;")
    lazy_users = [row[0] for row in cursor.fetchall()]
    if lazy_users:
        progress_report += "\n🚨 **Ленивцы (0% прогресса):**\n"
        for user in lazy_users:
            progress_report += f"❌ {user} - даже не начал работу!\n"

    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=progress_report)



# async def send_daily_summary(context: CallbackContext):
#     conn = get_db_connection()
#     cursor = conn.cursor()

#     # 1️⃣ Получаем список всех, кто делал переводы
#     cursor.execute("""
#         SELECT DISTINCT user_id, username 
#         FROM translations 
#         WHERE timestamp::date = CURRENT_DATE;
#     """)
#     active_users = {row[0]: row[1] for row in cursor.fetchall()}

#     # 2️⃣ Получаем список всех, кто писал хоть что-то в чат
#     cursor.execute("""
#         SELECT DISTINCT user_id, username
#         FROM messages
#         WHERE timestamp::date = CURRENT_DATE;
#     """)  
#     all_users = {row[0]: row[1] for row in cursor.fetchall()}

#     # 3️⃣ Получаем расширенную статистику переводов
#     cursor.execute("""
#         SELECT 
#             t.username, 
#             COUNT(t.id) AS переводов,
#             COALESCE(AVG(t.score), 0) AS средняя_оценка,
#             COALESCE(EXTRACT(EPOCH FROM (MAX(p.end_time) - MIN(p.start_time)))/60, 9999) AS время_в_минутах,
#             COALESCE(AVG(t.score), 0) - (COALESCE(EXTRACT(EPOCH FROM (MAX(p.end_time) - MIN(p.start_time)))/60, 9999) * 2) AS итоговый_балл
#         FROM translations t
#         JOIN user_progress p ON t.user_id = p.user_id
#         WHERE t.timestamp::date = CURRENT_DATE AND p.completed = TRUE
#         GROUP BY t.username
#         ORDER BY итоговый_балл DESC;
#     """)
#     rows = cursor.fetchall()

#     cursor.close()
#     conn.close()

#     # Если никто не делал переводов
#     if not rows:
#         await context.bot.send_message(chat_id=GROUP_CHAT_ID, text="📊 Да вы что охуели. Вы же нихуя за сегодня не сделали!")
#         return

#     summary = "📊 **Итоги дня:**\n\n"

#     # 🏆 Рейтинг лучших по итоговому баллу
#     medals = ["🥇", "🥈", "🥉"]  # Для топ-3
#     for i, (username, count, avg_score, minutes, final_score) in enumerate(rows):
#         medal = medals[i] if i < len(medals) else "🔹"
#         summary += f"{medal} {username}: **{count} переводов**, 🎯 {avg_score:.1f}/100, ⏱ {minutes:.1f} мин, 🏆 {final_score:.1f} баллов\n"

#     # 🚨 Ленивые, кто писал, но не перевел
#     lazy_users = {uid: uname for uid, uname in all_users.items() if uid not in active_users}
#     if lazy_users:
#         summary += "\n🚨 **Ленивые мудаки:**\n"
#         for username in lazy_users.values():
#             summary += f"👤 {username}: ленивое дерьмо\n"

#     await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=summary)




async def send_daily_summary(context: CallbackContext):
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1️⃣ Получаем список всех, кто делал переводы
    cursor.execute("""
        SELECT DISTINCT user_id, username 
        FROM translations 
        WHERE timestamp::date = CURRENT_DATE;
    """)
    active_users = {row[0]: row[1] for row in cursor.fetchall()}

    # 2️⃣ Получаем список всех, кто писал хоть что-то в чат
    cursor.execute("""
        SELECT DISTINCT user_id, username
        FROM messages
        WHERE timestamp::date = CURRENT_DATE;
    """)  
    all_users = {row[0]: row[1] for row in cursor.fetchall()}

    # 3️⃣ Получаем статистику переводов с учётом штрафов за время и пропуски
    cursor.execute("""
        SELECT 
            t.username, 
            COUNT(t.id) AS переведено,
            COALESCE(AVG(t.score), 0) AS средняя_оценка,
            COALESCE(SUM(EXTRACT(EPOCH FROM (p.end_time - p.start_time))/60), 9999) AS время_в_минутах,
            (SELECT COUNT(*) FROM daily_sentences WHERE date = CURRENT_DATE) - COUNT(t.id) AS пропущено,
            COALESCE(AVG(t.score), 0) 
                - (COALESCE(SUM(EXTRACT(EPOCH FROM (p.end_time - p.start_time))/60), 9999) * 2) 
                - ((SELECT COUNT(*) FROM daily_sentences WHERE date = CURRENT_DATE) - COUNT(t.id)) * 10 
                AS итоговый_балл
        FROM translations t
        JOIN user_progress p ON t.user_id = p.user_id
        WHERE t.timestamp::date = CURRENT_DATE AND p.completed = TRUE
        GROUP BY t.username
        ORDER BY итоговый_балл DESC;
    """)
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    # Если никто не сделал перевод
    if not rows:
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text="📊 Да вы что охуели. Вы же нихуя за сегодня не сделали!")
        return

    summary = "📊 **Итоги дня:**\n\n"

    # 🏆 Рейтинг лучших по итоговому баллу
    medals = ["🥇", "🥈", "🥉"]
    for i, (username, count, avg_score, minutes, missed, final_score) in enumerate(rows):
        medal = medals[i] if i < len(medals) else "🔹"
        summary += (
            f"{medal} {username}: **{count} переводов**, 🎯 {avg_score:.1f}/100, ⏱ {minutes:.1f} мин, "
            f"🚨 Пропущено: {missed}, 🏆 {final_score:.1f} баллов\n"
        )

    # 🚨 Ленивые, кто писал в чат, но не перевел
    lazy_users = {uid: uname for uid, uname in all_users.items() if uid not in active_users}
    if lazy_users:
        summary += "\n🚨 **Ленивые мудаки:**\n"
        for username in lazy_users.values():
            summary += f"👤 {username}: ленивое дерьмо\n"

    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=summary)



async def send_weekly_summary(context: CallbackContext):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Запрос статистики за 7 дней
    cursor.execute("""
        SELECT 
            t.username, 
            COUNT(t.id) AS всего_переводов,
            COALESCE(AVG(t.score), 0) AS средняя_оценка,
            COALESCE(SUM(EXTRACT(EPOCH FROM (p.end_time - p.start_time))/60), 9999) AS общее_время_в_минутах,
            COALESCE(AVG(t.score), 0) - (COALESCE(SUM(EXTRACT(EPOCH FROM (p.end_time - p.start_time))/60), 9999) * 2) AS итоговый_балл
        FROM translations t
        JOIN user_progress p ON t.user_id = p.user_id
        WHERE t.timestamp >= CURRENT_DATE - INTERVAL '7 days'
        AND p.completed = TRUE
        GROUP BY t.username
        ORDER BY итоговый_балл DESC;
    """)
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    if not rows:
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text="📊 Неделя прошла, но никто не перевел ни одного предложения!")
        return

    summary = "🏆 **Итоги недели:**\n\n"

    medals = ["🥇", "🥈", "🥉"]  # Для топ-3
    for i, (username, count, avg_score, total_minutes, final_score) in enumerate(rows):
        medal = medals[i] if i < len(medals) else "🔹"
        summary += f"{medal} {username}: **{count} переводов**, 🎯 {avg_score:.1f}/100, ⏱ {total_minutes:.1f} мин, 🏆 {final_score:.1f} баллов\n"

    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=summary)

    # 🔹 **Очищаем старые данные после отправки итогов недели**
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM translations WHERE timestamp < CURRENT_DATE - INTERVAL '7 days';")
    cursor.execute("DELETE FROM user_progress WHERE start_time < CURRENT_DATE - INTERVAL '7 days';")
    conn.commit()
    cursor.close()
    conn.close()




async def send_morning_tasks(context=None):
    conn = get_db_connection()
    cursor = conn.cursor()

    # 🔹 Find out how many sentences have already been sent today
    cursor.execute("SELECT COUNT(*) FROM daily_sentences WHERE date = CURRENT_DATE;")
    start_index = cursor.fetchone()[0]  # Number of already sent sentences

    # 🔹 Generate new sentences
    sentences = await get_original_sentences()
    tasks = []

    for i, sentence in enumerate(sentences, start=start_index + 1):  # Continue numbering
        tasks.append(f"{i}. {sentence}")
        cursor.execute(
            "INSERT INTO daily_sentences (date, sentence, unique_id) VALUES (CURRENT_DATE, %s, %s);",
            (sentence, i),
        )

    conn.commit()
    cursor.close()
    conn.close()

    # 🔹 Format the message
    message = f"🌅 **Here is your morning task!**\n\n" + "\n".join(tasks) + \
              "\n\nFormat your reply: `/translate <number> <your translation>`"

    if context:
        bot = context.bot
    else:
        bot = application.bot

    await bot.send_message(chat_id=GROUP_CHAT_ID, text=message)


# === Запуск бота ===
# def main():
#     application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
#     application.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Привет! Жди задания завтра!")))
#     application.add_handler(CommandHandler("newtasks", set_new_tasks))
#     application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
#     scheduler = BackgroundScheduler()
#     scheduler.add_job(lambda: application.create_task(send_morning_tasks(None)), "cron", hour=8, minute=0)
#     scheduler.add_job(lambda: application.create_task(send_morning_tasks(None)), "cron", hour=20, minute=55)
#     scheduler.add_job(lambda: application.create_task(send_morning_tasks(None)), "cron", hour=21, minute=31)
#     scheduler.add_job(lambda: application.create_task(send_morning_tasks(None)), "cron", hour=21, minute=50)
#     scheduler.add_job(lambda: application.create_task(send_morning_tasks(None)), "cron", hour=22, minute=20)
#     scheduler.add_job(lambda: application.create_task(send_daily_summary(None)), "cron", hour=23, minute=10)
#     scheduler.start()
    
#     application.run_polling()

# if __name__ == "__main__":
#     main()

import asyncio

# === Запуск бота ===
# def main():
#     global application
#     application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
#     application.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Hey! Wait until tomorow for your sentances!")))
#     application.add_handler(CommandHandler("newtasks", set_new_tasks))
#     application.add_handler(CommandHandler("translate", check_user_translation))
#     application.add_handler(CommandHandler("getmore", send_more_tasks))  # ✅ Adding /getmore command
#     application.add_handler(CommandHandler("letsgo", letsgo))
#     application.add_handler(CommandHandler("done", done))


    
#     # 🔹 Логирование всех сообщений (нужно для учета ленивых)
#     application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_message))  

#     scheduler = BackgroundScheduler()

#     def run_async_job(async_func, context=None):
#         if context is None:
#             context = CallbackContext(application=application)  # Создаем `context`, если его нет

#         try:
#             loop = asyncio.get_running_loop()  # ✅ Берем уже работающий event loop
#         except RuntimeError:
#             loop = asyncio.new_event_loop()  # ❌ В потоке `apscheduler` нет loop — создаем новый
#             asyncio.set_event_loop(loop)

#         loop.run_until_complete(async_func(context))  # ✅ Теперь event loop всегда работает

#     # 🔹 Запуск утренних заданий
#     scheduler.add_job(lambda: run_async_job(send_morning_tasks, CallbackContext(application=application)), "cron", hour=6, minute=1)
#     scheduler.add_job(lambda: run_async_job(send_morning_tasks, CallbackContext(application=application)), "cron", hour=15, minute=1)

    

#     # 🔹 Запуск итогов дня
#     scheduler.add_job(lambda: run_async_job(send_daily_summary, CallbackContext(application=application)), "cron", hour=22, minute=22)
#     #week results
#     scheduler.add_job(
#     lambda: run_async_job(send_weekly_summary, CallbackContext(application=application)), 
#     "cron", day_of_week="sun", hour=22, minute=0
# )


#     scheduler.start()
    
#     application.run_polling()

# if __name__ == "__main__":
#     main()

async def start(update: Update, context: CallbackContext):
    message = (
        "👋 **Привет!**\n"
        "Добро пожаловать в переводческий челлендж!\n\n"
        "📝 **Доступные команды:**\n"
        "✅ `/letsgo` - Начать перевод\n"
        "✅ `/done` - Завершить перевод (⚠️ потом подтвердите `/yes`!)\n"
        "✅ `/translate` - Отправить переводы\n"
        "✅ `/getmore` - Получить дополнительные предложения\n"
        "✅ `/stats` - Узнать свою статистику\n"
    )
    await update.message.reply_text(message)


async def user_stats(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    username = update.message.from_user.first_name

    conn = get_db_connection()
    cursor = conn.cursor()

    # 📌 Получаем статистику за сегодняшний день
    cursor.execute("""
        SELECT COUNT(t.id) AS переводов, 
               COALESCE(AVG(t.score), 0) AS средняя_оценка,
               COALESCE(SUM(EXTRACT(EPOCH FROM (p.end_time - p.start_time))/60), 9999) AS время_в_минутах,
               (SELECT COUNT(*) FROM daily_sentences WHERE date = CURRENT_DATE) - COUNT(t.id) AS пропущено,
               COALESCE(AVG(t.score), 0) 
                   - (COALESCE(SUM(EXTRACT(EPOCH FROM (p.end_time - p.start_time))/60), 9999) * 2) 
                   - ((SELECT COUNT(*) FROM daily_sentences WHERE date = CURRENT_DATE) - COUNT(t.id)) * 10 
                   AS итоговый_балл
        FROM translations t
        JOIN user_progress p ON t.user_id = p.user_id
        WHERE t.user_id = %s AND t.timestamp::date = CURRENT_DATE AND p.completed = TRUE
        GROUP BY t.user_id;
    """, (user_id,))
    
    today_stats = cursor.fetchone()

    # 📌 Получаем статистику за неделю
    cursor.execute("""
        SELECT COUNT(t.id) AS всего_переводов,
               COALESCE(AVG(t.score), 0) AS средняя_оценка,
               COALESCE(SUM(EXTRACT(EPOCH FROM (p.end_time - p.start_time))/60), 9999) AS общее_время_в_минутах,
               COALESCE(AVG(t.score), 0) 
                   - (COALESCE(SUM(EXTRACT(EPOCH FROM (p.end_time - p.start_time))/60), 9999) * 2) AS итоговый_балл
        FROM translations t
        JOIN user_progress p ON t.user_id = p.user_id
        WHERE t.user_id = %s AND t.timestamp >= CURRENT_DATE - INTERVAL '7 days'
        AND p.completed = TRUE
        GROUP BY t.user_id;
    """, (user_id,))
    
    weekly_stats = cursor.fetchone()

    cursor.close()
    conn.close()

    # Формируем ответ
    if today_stats:
        today_text = (
            f"📅 **Сегодняшняя статистика ({username})**\n"
            f"🔹 Переведено: {today_stats[0]}\n"
            f"🎯 Средняя оценка: {today_stats[1]:.1f}/100\n"
            f"⏱ Время: {today_stats[2]:.1f} мин\n"
            f"🚨 Пропущено: {today_stats[3]}\n"
            f"🏆 Итоговый балл: {today_stats[4]:.1f}\n"
        )
    else:
        today_text = f"📅 **Сегодняшняя статистика ({username})**\n❌ Нет данных (вы ещё не переводили)."

    if weekly_stats:
        weekly_text = (
            f"\n📆 **Статистика за неделю**\n"
            f"🔹 Переведено: {weekly_stats[0]}\n"
            f"🎯 Средняя оценка: {weekly_stats[1]:.1f}/100\n"
            f"⏱ Общее время: {weekly_stats[2]:.1f} мин\n"
            f"🏆 Итоговый балл: {weekly_stats[3]:.1f}\n"
        )
    else:
        weekly_text = "\n📆 **Статистика за неделю**\n❌ Нет данных."

    await update.message.reply_text(today_text + weekly_text)

import datetime
import pytz

async def debug_timezone(update: Update, context: CallbackContext):
    now_utc = datetime.datetime.now(pytz.utc)
    await update.message.reply_text(
        f"🕒 Текущее UTC-время на сервере: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}"
    )



def main():
    global application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))  # ✅ Теперь `/start` сразу выдаёт инфо
    application.add_handler(CommandHandler("newtasks", set_new_tasks))
    application.add_handler(CommandHandler("translate", check_user_translation))
    application.add_handler(CommandHandler("getmore", send_more_tasks))
    application.add_handler(CommandHandler("letsgo", letsgo))
    application.add_handler(CommandHandler("done", done))
    application.add_handler(CommandHandler("yes", confirm_done))
    application.add_handler(CommandHandler("stats", user_stats))  # ✅ Теперь можно смотреть статистику
    application.add_handler(CommandHandler("time", debug_timezone))


    # 🔹 Логирование всех сообщений (нужно для учета ленивых)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_message))  

    scheduler = BackgroundScheduler()

    def run_async_job(async_func, context=None):
        if context is None:
            context = CallbackContext(application=application)  # Создаем `context`, если его нет

        try:
            loop = asyncio.get_running_loop()  # ✅ Берем уже работающий event loop
        except RuntimeError:
            loop = asyncio.new_event_loop()  # ❌ В потоке `apscheduler` нет loop — создаем новый
            asyncio.set_event_loop(loop)

        loop.run_until_complete(async_func(context))  # ✅ Теперь event loop всегда работает

    # ✅ Добавляем задачу в `scheduler` ДЛЯ УТРА
    scheduler.add_job(
        lambda: run_async_job(send_morning_reminder, CallbackContext(application=application)),
        "cron", hour=6, minute=0
    )

    # ✅ Запуск утренних заданий
    scheduler.add_job(lambda: run_async_job(send_morning_tasks, CallbackContext(application=application)), "cron", hour=6, minute=1)
    scheduler.add_job(lambda: run_async_job(send_morning_tasks, CallbackContext(application=application)), "cron", hour=15, minute=1)

    # ✅ Запуск промежуточных итогов
    for hour in [9, 12, 15]:
        scheduler.add_job(
            lambda: run_async_job(send_progress_report, CallbackContext(application=application)),
            "cron", hour=hour, minute=0
        )

    # ✅ Запуск итогов дня
    scheduler.add_job(lambda: run_async_job(send_daily_summary, CallbackContext(application=application)), "cron", hour=23, minute=1)

    # ✅ Запуск итогов недели
    scheduler.add_job(
        lambda: run_async_job(send_weekly_summary, CallbackContext(application=application)), 
        "cron", day_of_week="sun", hour=22, minute=0
    )

    scheduler.start()
    
    application.run_polling()

if __name__ == "__main__":
    main()