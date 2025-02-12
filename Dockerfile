# Используем официальный образ Python
FROM python:3.12-slim

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# Копируем файлы проекта в контейнер
COPY . .

# Устанавливаем зависимости
RUN apt-get update && apt-get install -y procps
RUN pip install --no-cache-dir -r requirements.txt

# Указываем, что DATABASE_URL будет передаваться при запуске
ENV TELEGRAM_BOT_TOKEN=""
ENV OPENAI_API_KEY=""
ENV DATABASE_URL=""

# Запускаем бота
CMD ["python", "bot.py"]

