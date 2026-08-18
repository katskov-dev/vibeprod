FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py ./

EXPOSE 8933

HEALTHCHECK --interval=15s --timeout=3s --retries=6 \
  CMD python -c "import socket,sys; socket.create_connection(('127.0.0.1',8933),2); sys.exit(0)" || exit 1

# Сеть внутренняя (порт на хост не публикуется), воркеры обращаются по имени
# контейнера vibeprod-ssh:8933. Конфиг (серверы, ключи, белый список команд)
# тянется с брокера на каждый вызов инструмента — источник правды в админке.
CMD ["python", "server.py"]
