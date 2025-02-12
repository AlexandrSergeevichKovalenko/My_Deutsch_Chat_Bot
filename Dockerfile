# Используем официальный образ Python
FROM python:3.12-slim

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# Копируем файл requirements.txt перед копированием кода
COPY requirements.txt .

# Устанавливаем зависимости
RUN apt-get update && apt-get install -y procps
RUN pip install --no-cache-dir -r requirements.txt

# Теперь копируем остальные файлы проекта
COPY . .

# Указываем переменные окружения (значения передаются при запуске)
ENV TELEGRAM_BOT_TOKEN=""
ENV OPENAI_API_KEY=""
ENV DATABASE_URL=""

# Запускаем бота
CMD ["python", "bot.py"]


