FROM python:3.13-alpine
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py upgrade.py telegram_bot.py remote_servers.py ./
COPY assets ./assets
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
EXPOSE 8000 9000 18081
CMD ["python", "main.py"]
