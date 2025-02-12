import os
import logging
import openai
import os
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

# === Настройка OpenAI API ===
openai.api_key = os.getenv("OPENAI_API_KEY")
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
async def set_new_tasks(update: Update, context: CallbackContext):
    if update.message.chat.id != GROUP_CHAT_ID:
        return
    
    new_tasks = update.message.text.replace("/newtasks", "").strip().split("\n")
    if len(new_tasks) < 3:
        await update.message.reply_text("❌ Ошибка: Введите минимум 3 предложения через новую строку.")
        return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sentences;")
    for task in new_tasks:
        cursor.execute("INSERT INTO sentences (sentence) VALUES (%s);", (task,))
    conn.commit()
    cursor.close()
    conn.close()
    
    await update.message.reply_text("✅ Новые задания сохранены!")

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

async def send_morning_tasks(context=None):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Узнаем, сколько предложений уже отправлено за сегодня
    cursor.execute("SELECT COUNT(*) FROM daily_sentences WHERE date = CURRENT_DATE;")
    start_index = cursor.fetchone()[0]  # Количество уже отправленных предложений

    # Генерируем новые предложения
    sentences = await get_original_sentences()
    tasks = []
    
    for i, sentence in enumerate(sentences, start=start_index + 1):  # Уникальная нумерация
        tasks.append(f"{i}. {sentence}")
        cursor.execute(
            "INSERT INTO daily_sentences (date, sentence, unique_id) VALUES (CURRENT_DATE, %s, %s);",
            (sentence, i),
        )

    conn.commit()
    cursor.close()
    conn.close()

    # Формируем сообщение
    message = f"🌅 **Guten Morgen, малые! Ловите подачу:**\n\n" + "\n".join(tasks) + \
              "\n\nФормат ответа: `/translate <номер> <перевод>`"

    # Отправляем сообщение
    if context:
        bot = context.bot
    else:
        bot = application.bot

    await bot.send_message(chat_id=GROUP_CHAT_ID, text=message)

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

async def check_user_translation(update: Update, context: CallbackContext):
    message_text = update.message.text.strip()
    logging.info(f"📥 Получена команда: {message_text}")

    match = re.match(r"^/(перевод|translate)\s+(\d+)\s+(.+)$", message_text)
    if not match:
        logging.info(f"⚠️ Команда не распознана: {message_text}")
        await update.message.reply_text("❌ Ошибка: Используйте формат `/перевод <номер> <ваш перевод>`")
        return

    unique_id = int(match.group(2))  # Теперь это уникальный номер за день
    user_translation = match.group(3).strip()
    user_id = update.message.from_user.id
    username = update.message.from_user.first_name

    logging.info(f"✅ Распознано: Номер={unique_id}, Перевод={user_translation}")

    # Получаем оригинальный текст по его уникальному номеру
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT sentence FROM daily_sentences WHERE date = CURRENT_DATE AND unique_id = %s;",
        (unique_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row:
        await update.message.reply_text(f"❌ Ошибка: Предложение с номером {unique_id} не найдено в сегодняшних заданиях.")
        return

    original_text = row[0]

    # Логируем предложение перед проверкой перевода
    logging.info(f"📌 Проверка перевода. Оригинальное предложение: {original_text}")

    # Проверяем перевод через GPT
    feedback = await check_translation(original_text, user_translation)

    # Получаем оценку
    score_match = re.search(r"Оценка:\s*(\d+)/100", feedback)
    score = int(score_match.group(1)) if score_match else None

    # Записываем в базу данных
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO translations (user_id, username, sentence_id, user_translation, score, feedback)
        VALUES (%s, %s, %s, %s, %s, %s);
        """,
        (user_id, username, unique_id, user_translation, score, feedback),
    )
    conn.commit()
    cursor.close()
    conn.close()

    # Отправляем пользователю оценку
    await update.message.reply_text(
        f"👤 {username}, ваш перевод для {unique_id}-го предложения:\n"
        f"✅ Оценка: {feedback}"
    )



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

    # 3️⃣ Получаем статистику переводов
    cursor.execute("""
        SELECT username, COUNT(*), AVG(score) 
        FROM translations 
        WHERE timestamp::date = CURRENT_DATE 
        GROUP BY username 
        ORDER BY COUNT(*) DESC;
    """)
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    # Если никто не делал переводов
    if not rows:
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text="📊 Да вы что охуели. Вы же нихуя за сегодня не сделали!")
        return

    summary = "📊 **Итоги дня:**\n\n"
    
    # 4️⃣ Записываем всех, кто делал переводы
    for username, count, avg_score in rows:
        summary += f"👤 {username}: **{count} перевод(ов)**, средняя оценка: {avg_score:.1f}/100\n"

    # 5️⃣ Определяем "ленивых", кто писал в чат, но не переводил
    lazy_users = {uid: uname for uid, uname in all_users.items() if uid not in active_users}
    if lazy_users:
        summary += "\n🚨 **Ленивые мудаки:**\n"
        for username in lazy_users.values():
            summary += f"👤 {username}: ленивое дерьмо\n"

    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=summary)



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
def main():
    global application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Привет! Жди задания завтра!")))
    application.add_handler(CommandHandler("newtasks", set_new_tasks))
    application.add_handler(CommandHandler("translate", check_user_translation))
    
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

    # 🔹 Запуск утренних заданий
    scheduler.add_job(lambda: run_async_job(send_morning_tasks, CallbackContext(application=application)), "cron", hour=6, minute=1)
    # 🔹 Запуск итогов дня
    scheduler.add_job(lambda: run_async_job(send_daily_summary, CallbackContext(application=application)), "cron", hour=23, minute=28)

    scheduler.start()
    
    application.run_polling()

if __name__ == "__main__":
    main()

