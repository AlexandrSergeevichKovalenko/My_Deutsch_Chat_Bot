import psycopg2
import os

# Берём URL базы из переменной окружения
DATABASE_URL = os.getenv("DATABASE_URL")

try:
    # Подключаемся к базе
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    # Выполняем тестовый запрос
    cursor.execute("SELECT version();")
    db_version = cursor.fetchone()
    
    print("✅ Успешное подключение к PostgreSQL!")
    print("🔹 Версия базы данных:", db_version)

    cursor.close()
    conn.close()
except Exception as e:
    print("❌ Ошибка подключения к PostgreSQL:", e)
