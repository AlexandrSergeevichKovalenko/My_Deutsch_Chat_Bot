# Используем Python 3.10 (он совместим с TensorFlow)
FROM python:3.10-slim

WORKDIR /app

COPY . .

# Устанавливаем зависимости
COPY requirements.txt .
RUN apt-get update && apt-get install -y procps
RUN pip install --no-cache-dir -r requirements.txt

# Указываем переменные окружения
ENV TELEGRAM_BOT_TOKEN=""
ENV OPENAI_API_KEY=""
ENV DATABASE_URL=""

CMD ["python", "bot.py"]



