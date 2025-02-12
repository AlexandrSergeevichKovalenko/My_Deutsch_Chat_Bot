import testifavailablegpt4m
import logging
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

GROUP_CHAT_ID = -1002347376305  # Новый ID группы

# === Настройка OpenAI API ===
testifavailablegpt4m.api_key = os.getenv("OPENAI_API_KEY")
if not testifavailablegpt4m.api_key:
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

# === Логирование ===
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# === Функция для генерации новых предложений с помощью GPT-4 ===
async def generate_sentences():
    prompt = """
    Придумай 7 предложений уровня B2-C1 на русском языке для перевода на немецкий.
    Обязательно используй пассивный залог и Konjunktiv II.
    Выведи каждое предложение с новой строки.
    """
    
    response = await testifavailablegpt4m.ChatCompletion.acreate(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )
    sentences = response["choices"][0]["message"]["content"].split("\n")
    return [s.strip() for s in sentences if s.strip()]

async def get_original_sentences():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT sentence FROM sentences ORDER BY RANDOM() LIMIT 5;")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [row[0] for row in rows] if rows else await generate_sentences()

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
async def send_morning_tasks(context: CallbackContext):
    sentences = await generate_sentences()  # Вместо работы с БД вызываем GPT-4
    tasks = "\n".join([f"{i+1}. {sentence}" for i, sentence in enumerate(sentences)])
    message = f"🌅 **Доброе утро! Ваши задания:**\n\n{tasks}\n\nПереведите их на немецкий и отправьте в этот чат."
    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=message)

async def check_translation(original_text: str, user_translation: str):
    prompt = f"""
    Оригинальное предложение: "{original_text}"
    Перевод на немецкий: "{user_translation}"
    
    Оцени правильность перевода по шкале от 0 до 100 и объясни ошибки (если есть).
    """

    response = await testifavailablegpt4m.ChatCompletion.acreate(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )
    
    return response["choices"][0]["message"]["content"]


# === Оценка переводов ===
async def handle_message(update: Update, context: CallbackContext):
    sender = update.message.sender_chat or update.message.from_user

    print(f"🔵 Получено сообщение: {update.message.text} от {sender.id} в чате {update.message.chat.id}")
    print(f"📩 Полный апдейт: {update}")

    await update.message.reply_text("✅ Сообщение получено, но пока не обработано.")


# === Итог дня в 20:00 ===
async def send_daily_summary(context: CallbackContext):
    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text="📊 Итоги дня пока не реализованы.")

import asyncio

async def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Удаляем Webhook перед polling (важно!)
    await application.bot.delete_webhook(drop_pending_updates=True)

    async def start(update: Update, context: CallbackContext):
        print(f"📌 Чат ID: {update.message.chat.id} (GROUP_CHAT_ID = {GROUP_CHAT_ID})")
        print(f"🔵 Получена команда /start от {update.message.from_user.id} в чате {update.message.chat.id}")

        if update.message.chat.id != GROUP_CHAT_ID:
            print("🚨 Команда /start пришла не из целевой группы. Игнорируем.")
            return

        print("✅ Бот получил команду и сейчас отправит сообщение")  # Отладочный вывод
        await update.message.reply_text("Привет! Жди задания завтра!")
        print("✅ Ответ отправлен!")

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("newtasks", set_new_tasks))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: application.create_task(send_morning_tasks(None)), "cron", hour=8, minute=0)
    scheduler.add_job(lambda: application.create_task(send_morning_tasks(None)), "cron", hour=17, minute=25)
    scheduler.add_job(lambda: application.create_task(send_morning_tasks(None)), "cron", hour=18, minute=0)
    scheduler.add_job(lambda: application.create_task(send_morning_tasks(None)), "cron", hour=20, minute=0)
    scheduler.add_job(lambda: application.create_task(send_morning_tasks(None)), "cron", hour=22, minute=0)
    scheduler.add_job(lambda: application.create_task(send_daily_summary(None)), "cron", hour=23, minute=0)
    scheduler.start()

    print("Зарегистрированные команды:")
    for handler in application.handlers[0]:
        print(handler)

    print("✅ Бот запущен и слушает обновления!")

    async def log_updates(update: Update, context: CallbackContext):
        print(f"📩 ПОЛУЧЕНО ОБНОВЛЕНИЕ: {update}")

    application.add_handler(MessageHandler(filters.ALL, log_updates))  # Логируем ВСЕ обновления (в том числе /start)

    # Запускаем бота в режиме polling
    await application.run_polling()

if __name__ == "__main__":
    try:
        asyncio.run(main())  # 🚀 Современный способ запуска асинхронного кода
    except KeyboardInterrupt:
        print("Бот остановлен вручную")

#if __name__ == "__main__":
#    loop = asyncio.get_event_loop()

#    try:
#        task = loop.create_task(main())  # Запускаем main() как задачу
#        loop.run_forever()  # Оставляем event loop активным
#    except KeyboardInterrupt:
#        print("Бот остановлен вручную")
#    finally:
#        task.cancel()  # Отменяем задачу, если есть
#        loop.stop()
