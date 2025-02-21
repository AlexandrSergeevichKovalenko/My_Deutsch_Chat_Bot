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


# 🔹 Отладочный вывод, чтобы проверить, какой токен получен
print(f"DEBUG: TELEGRAM_BOT_TOKEN = {repr(TELEGRAM_BOT_TOKEN)}")

# 🔹 Проверяем, действительно ли переменная пуста
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ Ошибка: TELEGRAM_BOT_TOKEN не задан. Проверь переменные окружения!")

GROUP_CHAT_ID = -1002347376305  # ID вашей группы
#GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "").strip()

if not GROUP_CHAT_ID:
    raise ValueError("❌ Ошибка: GROUP_CHAT_ID не задан. Проверь переменные окружения!")
GROUP_CHAT_ID = int(GROUP_CHAT_ID)

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
            unique_id INT NOT NULL,
            user_id BIGINT  -- 🆕 Теперь указываем пользователя, которому назначено предложение
        );
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_progress (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            username TEXT,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            completed BOOLEAN DEFAULT FALSE,
            CONSTRAINT unique_user_session UNIQUE (user_id, start_time)
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
        "🌅 **Доброе утро, всем кроме Кончиты!**\n\n"
        "Чтобы принять участие в переводе, напишите команду `/letsgo`. После этого вам будут высланы предложения.\n\n"
        "📌 **Важно:**\n"
        "🔹 Переводите максимально точно и быстро — общение время вашего перевода будет отниматься от набранного вами среднего балла. Итог рассчитывается таким образом: суммируем все полученные баллы за перевод делим на количество переведённых предложений и вычитаем время (в минутах) потраченное на перевод всех предложений - чем дольше переводите тем больше штраф)!\n"
        "🔹 Команда `/letsgo` используется только для получения первой партии предложений. Если впоследствии захотите участвовать ещё пишите `/getmore`.\n"
        "🔹 После перевода всех предложений обязательно выполните `/done` и подтвердите окончание нажатием `/yes`.\n"
        "🔹 В 09:00, 12:00 и 15:00 будут **промежуточные итоги** по каждому участнику.\n"
        "🔹 Итоговые результаты дня отправляются в 22:00.\n\n"
    )  
    
    # 📌 Список команд
    commands = (
        "📜 **Доступные команды:**\n"
        "/letsgo - Получить первую партию заданий на перевод\n"
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

    # 🔹 **Проверяем, есть ли у пользователя активная сессия за сегодня**
    cursor.execute("""
        SELECT user_id FROM user_progress 
        WHERE user_id = %s AND start_time::date = CURRENT_DATE AND completed = FALSE;
    """, (user_id,))
    
    active_session = cursor.fetchone()

    if active_session is not None:
        await update.message.reply_text(
            "❌ Вы уже начали перевод! Завершите его перед повторным запуском. "
            "Если вы уже выполняли задания и хотите ещё, используйте '/getmore'."
        )
        cursor.close()
        conn.close()
        return

    # ✅ **Автоматически завершаем незавершённые сессии предыдущих дней**
    cursor.execute("""
        UPDATE user_progress 
        SET end_time = NOW(), completed = TRUE 
        WHERE user_id = %s AND start_time::date < CURRENT_DATE AND completed = FALSE;
    """, (user_id,))
    conn.commit()

    # ✅ **Создаём новую запись в `user_progress`, НЕ ЗАТИРАЯ старые сессии**
    cursor.execute("""
        INSERT INTO user_progress (user_id, username, start_time, completed) 
        VALUES (%s, %s, NOW(), FALSE);
    """, (user_id, username))
    conn.commit()

    # ✅ **Выдаём новые предложения**
    sentences = [s.strip() for s in await get_original_sentences() if s.strip()]

    if not sentences:
        await update.message.reply_text("❌ Ошибка: не удалось получить предложения. Попробуйте позже.")
        cursor.close()
        conn.close()
        return

    # Определяем стартовый индекс (если пользователь делал `/getmore`)
    cursor.execute("""
        SELECT COUNT(*) FROM daily_sentences WHERE date = CURRENT_DATE AND user_id = %s;
    """, (user_id,))
    last_index = cursor.fetchone()[0]  

    tasks = []
    for i, sentence in enumerate(sentences, start=last_index + 1):  
        cursor.execute("""
            INSERT INTO daily_sentences (date, sentence, unique_id, user_id) 
            VALUES (CURRENT_DATE, %s, %s, %s);
        """, (sentence, i, user_id))
        tasks.append(f"{i}. {sentence}")

    conn.commit()
    cursor.close()
    conn.close()

    logging.info(f"🚀 Пользователь {username} ({user_id}) начал перевод. Записано {len(tasks)} предложений.")

    tasks_text = "\n".join(tasks)
    await update.message.reply_text(
        f"🚀 **Вы начали перевод! Время пошло.**\n\n"
        f"📜 **Ваши предложения:**\n{tasks_text}\n\n"
        "✏️ **Отправьте все переводы и завершите с помощью** `/done`."
    )



async def done(update: Update, context: CallbackContext):
    user = update.message.from_user
    user_id = user.id

    conn = get_db_connection()
    cursor = conn.cursor()

    # 🔹 Проверяем, есть ли у пользователя активная сессия
    cursor.execute("""
        SELECT start_time, end_time, completed 
        FROM user_progress 
        WHERE user_id = %s AND completed = FALSE
        ORDER BY start_time DESC 
        LIMIT 1;
    """, (user_id,))
    
    row = cursor.fetchone()

    if not row:
        await update.message.reply_text("❌ У вас нет активных сессий! Используйте /letsgo, чтобы начать.")
        cursor.close()
        conn.close()
        return

    start_time, end_time, completed = row

    # ✅ Позволяем пользователю всегда завершать сессию вручную
    cursor.execute("""
        UPDATE user_progress 
        SET end_time = NOW(), completed = TRUE 
        WHERE user_id = %s AND completed = FALSE;
    """, (user_id,))
    conn.commit()

    # 🔹 Проверяем, все ли предложения переведены
    cursor.execute("""
        SELECT COUNT(*) FROM daily_sentences 
        WHERE date = CURRENT_DATE AND user_id = %s;
    """, (user_id,))
    total_sentences = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM translations 
        WHERE user_id = %s AND timestamp::date = CURRENT_DATE;
    """, (user_id,))
    translated_count = cursor.fetchone()[0]

    if translated_count < total_sentences:
        await update.message.reply_text(
            f"⚠️ Вы перевели {translated_count} из {total_sentences} предложений.\n"
            "Перевод завершён, но не все предложения переведены! Это повлияет на ваш итоговый балл."
        )
    else:
        await update.message.reply_text("✅ **Вы успешно завершили перевод! Все предложения переведены.**")

    cursor.close()
    conn.close()


async def force_finalize_sessions(context: CallbackContext = None):
    """Завершает ВСЕ незавершённые сессии только за сегодняшний день в 23:59."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE user_progress 
        SET end_time = NOW(), completed = TRUE
        WHERE completed = FALSE AND start_time::date = CURRENT_DATE;
    """)

    conn.commit()
    cursor.close()
    conn.close()

    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text="🔔 **Все незавершённые сессии за сегодня автоматически закрыты!**")




async def auto_finalize_sessions():
    """Каждые 2 минуты проверяет незавершённые переводы и завершает их, если есть переводы."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE user_progress 
        SET end_time = NOW(), completed = TRUE
        WHERE completed = FALSE
        AND user_id IN (SELECT DISTINCT user_id FROM translations WHERE timestamp::date = CURRENT_DATE);
    """)
    
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
    - Используй **пассивный залог** и **Konjunktiv II** В 30% предложений.
    - Каждое предложение должно быть **на отдельной строке**.
    - **НЕ добавляй перевод!** Только оригинальные русские предложения.
    - Предложения должны содержать часто употребительную в повседневной жизни лексику(бизнес медицина, Хобби, Свободное время, Учёба, Работа, Путешествия) и грамматику.

    **Пример формата вывода:**
    Этот город был основан более 300 лет назад.
    Если бы у нас было больше времени, мы бы посетили все музеи.
    Важно, чтобы все решения были приняты коллегиально.
    Книга была написана известным писателем в прошлом веке.
    Было бы лучше, если бы он согласился на это предложение.
    Нам сказали, что проект будет завершен через неделю.
    Если бы он мог говорить на немецком, он бы легко нашел работу.
    Сделал работу он пошёл отдыхать.
    Зная о вежливости немцев я выбрал вежливую формулировку.
    Не зная его лично, его поступок невозможно понять.
    Чтобы закончить работу вовремя, Я поспешил.
    Учитывая правила вежливости, он говорил сдержанно.

    """

    for attempt in range(5):  # Пробуем до 5 раз при ошибке
        try:
            response = await client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[{"role": "user", "content": prompt}]
            )
            sentences = response.choices[0].message.content.split("\n")
            filtered_sentences = [s.strip() for s in sentences if s.strip()]  # ✅ Фильтруем пустые строки
            if filtered_sentences:
                return filtered_sentences
        except openai.RateLimitError:
            wait_time = (attempt + 1) * 2  # Задержка: 2, 4, 6 сек...
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



application = None  # Глобальная переменная для хранения объекта бота





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
        ON CONFLICT (user_id, start_time) DO UPDATE 
        SET start_time = NOW(), completed = FALSE;
        """,
        (user_id, username)
    )

    # 🔹 **Определяем стартовый индекс**
    cursor.execute("SELECT COUNT(*) FROM daily_sentences WHERE date = CURRENT_DATE AND user_id = %s;", (user_id,))
    last_index = cursor.fetchone()[0]  # Количество уже выданных предложений пользователю

    # 🔹 Генерируем новые предложения
    sentences = await get_original_sentences()
    tasks = []

    for i, sentence in enumerate(sentences, start=last_index + 1):  # **Исправлено!**
        if not sentence.strip(): # ✅ Пропускаем пустые строки
            continue
        cursor.execute(
            "INSERT INTO daily_sentences (date, sentence, unique_id, user_id) VALUES (CURRENT_DATE, %s, %s, %s);",
            (sentence, i, user_id),
        )
        tasks.append(f"{i}. {sentence}")  # **Теперь нумерация корректная!**

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
    1. **Выставь оценку от 0 до 100** в соответствии оригинальному содержанию, правильному набору лексики, корректности грамматической конструкции(при выставлении оценки это наиболее весомый критерий) и стиля. При полном несоответствии содержанию оценка ноль).
    2. **Обязательно объясни как должна правильно строится основная грамматическая конструкция данного предложения** только если предложение было написано некорректно грамматически.
    3. **Обязательно укажи правильный вариант перевода (это должен быть наиболее часто встречаемый максимально аутентичный перевод)**.
    4. Для наиболее часто встречаемых слов уровня B2-C1 укажи два синонима через запятую в формате: Синонимы:....

    **Формат ответа (без лишних символов, только текст!)**:
    Оценка: X/100
    Ошибки: ...
    Верный перевод: ...
    Синонимы/Антонимы: ...
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
    pattern = re.compile(r"(\d+)\.\s*(.+)")
    translations = pattern.findall(translations_text)

    if not translations:
        await update.message.reply_text("❌ Ошибка: Используйте формат: \n\n/translate\n1. <перевод>\n2. <перевод>")
        return

    user_id = update.message.from_user.id
    username = update.message.from_user.first_name

    conn = get_db_connection()
    cursor = conn.cursor()

    # 🔹 Получаем **ID предложений, которые принадлежат пользователю**
    cursor.execute(
        "SELECT unique_id FROM daily_sentences WHERE date = CURRENT_DATE AND user_id = %s;", 
        (user_id,)
    )
    allowed_sentences = {row[0] for row in cursor.fetchall()}  # Собираем в set() для быстрого поиска

    results = []  # Храним результаты для Telegram

    for number_str, user_translation in translations:
        sentence_number = int(number_str)

        # 🔹 **Проверяем, принадлежит ли это предложение пользователю**
        if sentence_number not in allowed_sentences:
            results.append(f"❌ Ошибка: Предложение {sentence_number} вам не принадлежит!")
            continue

        # 🔹 **Получаем оригинальный текст предложения**
        cursor.execute(
            "SELECT id, sentence FROM daily_sentences WHERE date = CURRENT_DATE AND unique_id = %s AND user_id = %s;",
            (sentence_number, user_id),
        )
        row = cursor.fetchone()

        if not row:
            results.append(f"❌ Ошибка: Предложение {sentence_number} не найдено.")
            continue

        sentence_id, original_text = row

        # 🔹 **Проверяем, отправлял ли этот пользователь перевод этого предложения**
        cursor.execute(
            "SELECT id FROM translations WHERE user_id = %s AND sentence_id = %s AND timestamp::date = CURRENT_DATE;",
            (user_id, sentence_id)
        )
        existing_translation = cursor.fetchone()

        if existing_translation:
            results.append(f"⚠️ Вы уже переводили предложение {sentence_number}. Только первый перевод учитывается!")
            continue

        logging.info(f"📌 Проверяем перевод №{sentence_number}: {user_translation}")

        # 🔹 **Проверяем перевод через GPT**
        MAX_FEEDBACK_LENGTH = 1000  # Ограничим длину комментария GPT
        feedback = await check_translation(original_text, user_translation)

        # Получаем оценку из строки "Оценка: 85/100"
        score_match = re.search(r"Оценка:\s*(\d+)/100", feedback)
        score = int(score_match.group(1)) if score_match else None

        # 🔹 **Сохраняем перевод в базу**
        cursor.execute(
            "INSERT INTO translations (user_id, username, sentence_id, user_translation, score, feedback) "
            "VALUES (%s, %s, %s, %s, %s, %s);",
            (user_id, username, sentence_id, user_translation, score, feedback),
        )

        conn.commit()

        # Обрезаем, если слишком длинный
        if len(feedback) > MAX_FEEDBACK_LENGTH:
            feedback = feedback[:MAX_FEEDBACK_LENGTH] + "...\n⚠️ Ответ GPT был сокращён."

        results.append(f"📜 **Предложение {sentence_number}**\n🎯 Оценка: {feedback}")

    cursor.close()
    conn.close()

    # Отправляем пользователю результаты всех переводов
    # Разбиваем сообщение, если оно длинное
    MAX_MESSAGE_LENGTH = 4000  # Чтобы не рисковать, оставляем небольшой запас
    message_text = "\n\n".join(results)

    if len(message_text) > MAX_MESSAGE_LENGTH:
        parts = [message_text[i:i+MAX_MESSAGE_LENGTH] for i in range(0, len(message_text), MAX_MESSAGE_LENGTH)]
        for part in parts:
            await update.message.reply_text(part)
    else:
        logging.info(f"📩 Длина сообщения: {len(message_text)} символов")
        await update.message.reply_text(message_text)






async def send_progress_report(context: CallbackContext):
    conn = get_db_connection()
    cursor = conn.cursor()

    # 🔹 Получаем всех пользователей, которые писали в чат **за месяц**
    cursor.execute("""
        SELECT DISTINCT user_id, username 
        FROM messages 
        WHERE timestamp >= date_trunc('month', CURRENT_DATE);
    """)
    all_users = {row[0]: row[1] for row in cursor.fetchall()}

    # 🔹 Получаем всех, кто перевёл хотя бы одно предложение **за сегодня**
    cursor.execute("""
        SELECT DISTINCT user_id FROM translations WHERE timestamp::date = CURRENT_DATE;
    """)
    active_users = {row[0] for row in cursor.fetchall()}

    # 🔹 Собираем статистику по пользователям **за сегодня**(checked)
    cursor.execute("""
        SELECT 
        ds.user_id,
        COUNT(DISTINCT ds.id) AS всего_предложений,
        COUNT(DISTINCT t.id) AS переведено,
        (COUNT(DISTINCT ds.id) - COUNT(DISTINCT t.id)) AS пропущено,
        COALESCE(p.avg_time, 0) AS среднее_время_сессии_в_минутах, -- ✅ Среднее время за день
        COALESCE(p.total_time, 0) AS общее_время_за_день, -- ✅ Общее время за день
        COALESCE(AVG(t.score), 0) AS средняя_оценка,
        COALESCE(AVG(t.score), 0) 
            - (COALESCE(p.avg_time, 0) * 1) -- ✅ Используем среднее время в расчётах
            - ((COUNT(DISTINCT ds.id) - COUNT(DISTINCT t.id)) * 20) AS итоговый_балл
    FROM daily_sentences ds
    LEFT JOIN translations t ON ds.user_id = t.user_id AND ds.id = t.sentence_id
    LEFT JOIN (
        SELECT user_id, 
            AVG(EXTRACT(EPOCH FROM (end_time - start_time))/60) AS avg_time, -- ✅ Среднее время сессии за день
            SUM(EXTRACT(EPOCH FROM (end_time - start_time))/60) AS total_time -- ✅ Общее время за день
        FROM user_progress
        WHERE completed = TRUE 
            AND start_time::date = CURRENT_DATE -- ✅ Теперь только за день
        GROUP BY user_id
    ) p ON ds.user_id = p.user_id
    WHERE ds.date = CURRENT_DATE
    GROUP BY ds.user_id, p.avg_time, p.total_time
    ORDER BY итоговый_балл DESC;
    """)
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    # 🔹 Формируем отчёт
    if not rows:
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text="📊 Сегодня никто не перевёл ни одного предложения!")
        return

    progress_report = "📊 **Промежуточные итоги перевода:**\n\n"

    for user_id, total, translated, missed, avg_minutes, total_minutes, avg_score, final_score in rows:
        progress_report += (
            f"👤 **{all_users.get(user_id, 'Неизвестный пользователь')}**\n"
            f"📜 Переведено: **{translated}/{total}**\n"
            f"🚨 Не переведено: **{missed}**\n"
            f"⏱ Время среднее: **{avg_minutes:.1f} мин**\n"
            f"⏱ Время общ.: **{total_minutes:.1f} мин**\n"
            f"🎯 Средняя оценка: **{avg_score:.1f}/100**\n"
            f"🏆 Итоговый балл: **{final_score:.1f}**\n\n"
        )

    # 🚨 **Добавляем блок про ленивых (учитываем всех, кто писал в чат за месяц)**
    lazy_users = {uid: uname for uid, uname in all_users.items() if uid not in active_users}
    if lazy_users:
        progress_report += "\n🚨 **Ленивцы (писали в чат, но не переводили):**\n"
        for username in lazy_users.values():
            progress_report += f"👤 {username}: ничего не перевёл!\n"

    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=progress_report)




#SQL Запрос проверено
async def send_daily_summary(context: CallbackContext):

    conn = get_db_connection()
    cursor = conn.cursor()

    # 🔹 Собираем активных пользователей (кто перевёл хотя бы одно предложение)
    cursor.execute("""
        SELECT DISTINCT user_id, username 
        FROM translations 
        WHERE timestamp::date = CURRENT_DATE;
    """)
    active_users = {row[0]: row[1] for row in cursor.fetchall()}

    # 🔹 Собираем всех, кто хоть что-то писал в чат
    cursor.execute("""
        SELECT DISTINCT user_id, username
        FROM messages
        WHERE timestamp >= date_trunc('month', CURRENT_DATE);
    """)
    all_users = {row[0]: row[1] for row in cursor.fetchall()}

    # 🔹 Собираем статистику за день
    cursor.execute("""
       SELECT 
            ds.user_id, 
            COUNT(DISTINCT ds.id) AS total_sentences,
            COUNT(DISTINCT t.id) AS translated,
            (COUNT(DISTINCT ds.id) - COUNT(DISTINCT t.id)) AS missed,
            COALESCE(p.avg_time, 0) AS avg_time_minutes, 
            COALESCE(p.total_time, 0) AS total_time_minutes, 
            COALESCE(AVG(t.score), 0) AS avg_score,
            COALESCE(AVG(t.score), 0) 
            - (COALESCE(p.avg_time, 0) * 1) 
            - ((COUNT(DISTINCT ds.id) - COUNT(DISTINCT t.id)) * 20) AS final_score
        FROM daily_sentences ds
        LEFT JOIN translations t ON ds.user_id = t.user_id AND ds.id = t.sentence_id
        LEFT JOIN (
            SELECT user_id, 
                AVG(EXTRACT(EPOCH FROM (end_time - start_time))/60) AS avg_time, 
                SUM(EXTRACT(EPOCH FROM (end_time - start_time))/60) AS total_time
            FROM user_progress
            WHERE completed = true
        		AND start_time::date = CURRENT_DATE -- ✅ Теперь только за день
            GROUP BY user_id
        ) p ON ds.user_id = p.user_id
        WHERE ds.date = CURRENT_DATE
        GROUP BY ds.user_id, p.avg_time, p.total_time
        ORDER BY final_score DESC;
    """)
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    # 🔹 Формируем итоговый отчёт
    if not rows:
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text="📊 Сегодня никто не перевёл ни одного предложения!")
        return

    summary = "📊 **Итоги дня:**\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, (user_id, total_sentences, translated, missed, avg_minutes, total_time_minutes, avg_score, final_score) in enumerate(rows):
        username = all_users.get(user_id, 'Неизвестный пользователь')  # ✅ Берём имя пользователя из словаря
        medal = medals[i] if i < len(medals) else "💩"
        summary += (
            f"{medal} **{username}**\n"
            f"📜 Всего предложений: **{total_sentences}**\n"
            f"✅ Переведено: **{translated}**\n"
            f"🚨 Не переведено: **{missed}**\n"
            f"⏱ Время среднее: **{avg_minutes:.1f} мин**\n"
            f"⏱ Время общее: **{total_time_minutes:.1f} мин**\n"
            f"🎯 Средняя оценка: **{avg_score:.1f}/100**\n"
            f"🏆 Итоговый балл: **{final_score:.1f}**\n\n"
        )


    # 🚨 **Добавляем блок про ленивых**
    lazy_users = {uid: uname for uid, uname in all_users.items() if uid not in active_users}
    if lazy_users:
        summary += "\n🚨 **Ленивцы (писали в чат, но не переводили):**\n"
        for username in lazy_users.values():
            summary += f"👤 {username}: ничего не перевёл!\n"

    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=summary)




#SQL Запрос проверено
async def send_weekly_summary(context: CallbackContext):

    conn = get_db_connection()
    cursor = conn.cursor()

    # 🔹 Собираем статистику за неделю
    cursor.execute("""
        SELECT 
        t.username, 
        COUNT(DISTINCT t.sentence_id) AS всего_переводов,
        COALESCE(AVG(t.score), 0) AS средняя_оценка,
        COALESCE(p.avg_time, 0) AS среднее_время_сессии_в_минутах, -- ✅ Среднее время сессии
        COALESCE(p.total_time, 0) AS общее_время_в_минутах, -- ✅ Теперь есть и общее время
        (SELECT COUNT(*) 
        FROM daily_sentences 
        WHERE date >= CURRENT_DATE - INTERVAL '7 days' 
        AND user_id = t.user_id) 
        - COUNT(DISTINCT t.sentence_id) AS пропущено_за_неделю,
        COALESCE(AVG(t.score), 0) 
            - (COALESCE(p.avg_time, 0) * 1) -- ✅ Среднее время в штрафе
            - ((SELECT COUNT(*) 
                FROM daily_sentences 
                WHERE date >= CURRENT_DATE - INTERVAL '7 days' 
                AND user_id = t.user_id) 
            - COUNT(DISTINCT t.sentence_id)) * 20
            AS итоговый_балл
    FROM translations t
    LEFT JOIN (
        SELECT user_id, 
            AVG(EXTRACT(EPOCH FROM (end_time - start_time))/60) AS avg_time, -- ✅ Среднее время сессии
            SUM(EXTRACT(EPOCH FROM (end_time - start_time))/60) AS total_time -- ✅ Общее время
        FROM user_progress 
        WHERE completed = TRUE 
        AND start_time >= CURRENT_DATE - INTERVAL '7 days'
        GROUP BY user_id
    ) p ON t.user_id = p.user_id
    WHERE t.timestamp >= CURRENT_DATE - INTERVAL '7 days'
    GROUP BY t.username, t.user_id, p.avg_time, p.total_time
    ORDER BY итоговый_балл DESC;

    """)
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    if not rows:
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text="📊 Неделя прошла, но никто не перевел ни одного предложения!")
        return

    summary = "🏆 **Итоги недели:**\n\n"

    medals = ["🥇", "🥈", "🥉"]
    for i, (username, count, avg_score, avg_minutes, total_minutes, missed, final_score) in enumerate(rows):
        medal = medals[i] if i < len(medals) else "💩"
        summary += (
            f"{medal} **{username}**\n"
            f"📜 Переведено: **{count}**\n"
            f"🎯 Средняя оценка: **{avg_score:.1f}/100**\n"
            f"⏱ Время среднее: **{avg_minutes:.1f} мин**\n"
            f"⏱ Время общее: **{total_minutes:.1f} мин**\n"
            f"🚨 Пропущено: **{missed}**\n"
            f"🏆 Итоговый балл: **{final_score:.1f}**\n\n"
        )

    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=summary)




async def send_morning_tasks(context: CallbackContext):
    message = (
        "🌅 ** Не забудьте начать перевод!**\n\n"
        "Используйте для этого команду `/letsgo`.\n"
        "После этого вам будут отправлены индивидуальные предложения.\n\n"
        "📝 **Команды на день:**\n"
        "✅ `/letsgo` - Получить задания\n"
        "✅ `/done` - Завершить перевод (⚠️ подтвердите `/yes`!)\n"
        "✅ `/translate` - Отправить переводы\n"
        "✅ `/getmore` - Получить дополнительные предложения\n"
        "✅ `/stats` - Узнать свою статистику\n"
    )

    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=message)



import asyncio


async def start(update: Update, context: CallbackContext):
    message = (
        "👋 **Привет всем кроме Konchita!**\n"
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

    # 📌 Статистика за сегодняшний день (обновлено для среднего времени)
    cursor.execute("""
        SELECT 
            COUNT(DISTINCT t.sentence_id) AS переведено,  
            COALESCE(AVG(t.score), 0) AS средняя_оценка,
            COALESCE((
                SELECT AVG(EXTRACT(EPOCH FROM (p.end_time - p.start_time)) / 60)  -- ✅ Используем AVG вместо SUM
                FROM user_progress p
                WHERE p.user_id = t.user_id 
                    AND p.start_time::date = CURRENT_DATE
                    AND p.completed = TRUE
            ), 0) AS среднее_время_сессии_в_минутах,  -- ✅ Обновили название, чтобы было понятно
            GREATEST(0, (SELECT COUNT(*) FROM daily_sentences 
                        WHERE date = CURRENT_DATE AND user_id = t.user_id) - COUNT(DISTINCT t.sentence_id)) AS пропущено,
            COALESCE(AVG(t.score), 0) 
                - (COALESCE((
                    SELECT AVG(EXTRACT(EPOCH FROM (p.end_time - p.start_time)) / 60)  -- ✅ Здесь тоже AVG
                    FROM user_progress p
                    WHERE p.user_id = t.user_id 
                        AND p.start_time::date = CURRENT_DATE
                        AND p.completed = TRUE
                ), 0) * 1) 
                - (GREATEST(0, (SELECT COUNT(*) FROM daily_sentences 
                                WHERE date = CURRENT_DATE AND user_id = t.user_id) - COUNT(DISTINCT t.sentence_id)) * 20) AS итоговый_балл
        FROM translations t
        WHERE t.user_id = %s AND t.timestamp::date = CURRENT_DATE
        GROUP BY t.user_id;
    """, (user_id,))

    today_stats = cursor.fetchone()

    # 📌 Недельная статистика (обновлено для среднего времени)
    cursor.execute("""
        SELECT 
            t.user_id,
            COUNT(DISTINCT t.sentence_id) AS всего_переводов,
            COALESCE(AVG(t.score), 0) AS средняя_оценка,
            COALESCE(p.avg_session_time, 0) AS среднее_время_сессии_в_минутах,  
            COALESCE(p.total_time, 0) AS общее_время_за_неделю,  
            GREATEST(0, COALESCE(ds.total_sentences, 0) - COUNT(DISTINCT t.sentence_id)) AS пропущено_за_неделю,
            COALESCE(AVG(t.score), 0) 
                - (COALESCE(p.avg_session_time, 0) * 2)  
                - (GREATEST(0, COALESCE(ds.total_sentences, 0) - COUNT(DISTINCT t.sentence_id)) * 20) AS итоговый_балл
        FROM translations t
        LEFT JOIN (
            -- ✅ Отдельный подзапрос для корректного расчёта времени по каждому пользователю
            SELECT 
                user_id, 
                AVG(EXTRACT(EPOCH FROM (end_time - start_time)) / 60) AS avg_session_time, 
                SUM(EXTRACT(EPOCH FROM (end_time - start_time)) / 60) AS total_time 
            FROM user_progress
            WHERE completed = TRUE 
                AND start_time >= CURRENT_DATE - INTERVAL '7 days'
            GROUP BY user_id
        ) p ON t.user_id = p.user_id
        LEFT JOIN (
            SELECT user_id, COUNT(*) AS total_sentences
            FROM daily_sentences
            WHERE date >= CURRENT_DATE - INTERVAL '7 days'
            GROUP BY user_id
        ) ds ON t.user_id = ds.user_id
        WHERE t.timestamp >= CURRENT_DATE - INTERVAL '7 days' 
            AND t.user_id = %s  -- ✅ Фильтр по конкретному пользователю
        GROUP BY t.user_id, p.avg_session_time, p.total_time, ds.total_sentences;
    """, (user_id,))

    weekly_stats = cursor.fetchone()

    cursor.close()
    conn.close()

    # 📌 Формирование ответа
    if today_stats:
        today_text = (
            f"📅 **Сегодняшняя статистика ({username})**\n"
            f"🔹 Переведено: {today_stats[0]}\n"
            f"🎯 Средняя оценка: {today_stats[1]:.1f}/100\n"
            f"⏱ Среднее время сессии: {today_stats[2]:.1f} мин\n"
            f"🚨 Пропущено: {today_stats[3]}\n"
            f"🏆 Итоговый балл: {today_stats[4]:.1f}\n"
        )
    else:
        today_text = f"📅 **Сегодняшняя статистика ({username})**\n❌ Нет данных (вы ещё не переводили)."

    if weekly_stats:
        weekly_text = (
            f"\n📆 **Статистика за неделю**\n"
            f"🔹 Переведено: {weekly_stats[1]}\n"
            f"🎯 Средняя оценка: {weekly_stats[2]:.1f}/100\n"
            f"⏱ Среднее время сессии: {weekly_stats[3]:.1f} мин\n"
            f"⏱ Общее время за неделю: {weekly_stats[4]:.1f} мин\n"
            f"🚨 Пропущено за неделю: {weekly_stats[5]}\n"
            f"🏆 Итоговый балл: {weekly_stats[6]:.1f}\n"
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


from telegram import Update
from telegram.ext import CommandHandler, CallbackContext
import psycopg2
import os

# Функция для сброса данных по ID
def reset_user_data(user_id):
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cursor = conn.cursor()

    # Удаляем переводы за сегодня
    cursor.execute("DELETE FROM translations WHERE user_id = %s AND timestamp::date = CURRENT_DATE;", (user_id,))

    # Удаляем прогресс пользователя
    cursor.execute("DELETE FROM user_progress WHERE user_id = %s;", (user_id,))

    conn.commit()
    cursor.close()
    conn.close()

# Обработчик команды /resetme <ID>
# === Функция для очистки данных пользователя ===
import datetime

# === Функция для очистки данных пользователя ===
def reset_user_data(user_id, date=None):
    """Удаляет данные пользователя за указанный день (или за сегодня, если дата не указана)"""
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cursor = conn.cursor()

    # Если дата не указана, используем сегодняшнюю
    if date is None:
        date = datetime.date.today()
    
    # Удаляем переводы за указанную дату
    cursor.execute("""
        DELETE FROM translations 
        WHERE user_id = %s AND timestamp::date = %s;
    """, (user_id, date))

    # Удаляем записи о прогрессе пользователя за указанную дату
    cursor.execute("""
        DELETE FROM user_progress 
        WHERE user_id = %s AND start_time::date = %s;
    """, (user_id, date))

    # Удаляем предложения, выданные пользователю за указанную дату
    cursor.execute("""
        DELETE FROM daily_sentences 
        WHERE user_id = %s AND date = %s;
    """, (user_id, date))

    conn.commit()
    cursor.close()
    conn.close()

# === Обработчик команды /resetme (для очистки данных) ===
async def reset_user_command(update: Update, context: CallbackContext):
    user = update.message.from_user
    chat_id = update.message.chat_id

    # Проверяем, передан ли ID пользователя (для админа)
    if context.args:
        ADMIN_ID = 117649764  # Заменить на свой Telegram ID
        if user.id != ADMIN_ID:
            await update.message.reply_text("❌ У вас нет прав на выполнение этой команды!")
            return

        try:
            user_id = int(context.args[0])  # Первый аргумент — ID пользователя
        except ValueError:
            await update.message.reply_text("❌ Ошибка: ID должен быть числом!")
            return

        # Если передан второй аргумент, пытаемся считать дату
        if len(context.args) > 1:
            try:
                date = datetime.datetime.strptime(context.args[1], "%Y-%m-%d").date()
            except ValueError:
                await update.message.reply_text("❌ Ошибка: Неверный формат даты! Используйте YYYY-MM-DD.")
                return
        else:
            date = None  # Если дата не указана, сбрасываем за сегодня

    else:
        # Если ID не указан, сбрасываем данные **самого пользователя** за сегодня
        user_id = user.id
        date = None  # По умолчанию сбрасываем за сегодня

    # Выполняем сброс данных
    reset_user_data(user_id, date)
    date_text = f"за {date}" if date else "за сегодня"
    await update.message.reply_text(f"✅ Данные пользователя {user_id} {date_text} сброшены!")
    print(f"✅ Данные пользователя {user_id} {date_text} сброшены!")






def main():
    global application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))  
    application.add_handler(CommandHandler("newtasks", set_new_tasks))
    application.add_handler(CommandHandler("translate", check_user_translation))
    application.add_handler(CommandHandler("getmore", send_more_tasks))
    application.add_handler(CommandHandler("letsgo", letsgo))
    application.add_handler(CommandHandler("done", done))
    application.add_handler(CommandHandler("stats", user_stats))  
    application.add_handler(CommandHandler("time", debug_timezone))
    application.add_handler(CommandHandler("reset", reset_user_command))  

    # 🔹 Логирование всех сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_message))  

    scheduler = BackgroundScheduler()

    def run_async_job(async_func, context=None):
        """Запускает асинхронную функцию внутри APScheduler."""
        if context is None:
            context = CallbackContext(application=application)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(async_func(context))

    # ✅ Утренняя рассылка
    scheduler.add_job(lambda: run_async_job(send_morning_reminder), "cron", hour=5, minute=0)

    # ✅ Утренние задания
    scheduler.add_job(lambda: run_async_job(send_morning_tasks), "cron", hour=7, minute=1)
    scheduler.add_job(lambda: run_async_job(send_morning_tasks), "cron", hour=15, minute=1)

    # ✅ Промежуточные итоги
    for hour in [6, 12, 18]:
        scheduler.add_job(lambda: run_async_job(send_progress_report), "cron", hour=hour, minute=0)

    # ✅ Итоги дня
    scheduler.add_job(lambda: run_async_job(send_daily_summary), "cron", hour=21, minute=1)

    # ✅ Итоги недели
    scheduler.add_job(lambda: run_async_job(send_weekly_summary), "cron", day_of_week="sun", hour=20, minute=0)

    # ✅ Автозавершение сессий в 23:59
    scheduler.add_job(lambda: run_async_job(force_finalize_sessions), "cron", hour=23, minute=59)

    scheduler.start()
    print("🚀 Бот запущен! Ожидаем сообщения...")
    application.run_polling()

# ✅ Вызов main() для запуска бота
if __name__ == "__main__":
    main()



