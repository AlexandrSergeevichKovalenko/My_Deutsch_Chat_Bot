# Как убедиться, что файл firebase_credentials.json загружается правильно?
import firebase_admin
from firebase_admin import credentials

try:
    cred = credentials.Certificate("telegramtranslatorbot-3a54f-firebase-adminsdk-fbsvc-2142b25473.json")
    print("✅ Файл `telegramtranslatorbot-3a54f-firebase-adminsdk-fbsvc-2142b25473.json` найден и загружен успешно!")
except Exception as e:
    print("❌ Ошибка: Файл `firebase_credentials.json` не найден!")
    print(f"Детали ошибки: {e}")
