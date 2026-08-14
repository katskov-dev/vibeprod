FROM mcr.microsoft.com/playwright:latest

RUN npm install -g @playwright/mcp@latest \
 && playwright-mcp install-browser chrome-for-testing

EXPOSE 8931

HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/8931' || exit 1

# --allowed-hosts '*' — сеть внутренняя (порт на хост не публикуется),
# воркеры обращаются по имени контейнера harness-playwright:8931.
# --browser chromium — сборка chrome-for-testing (Google Chrome в образе нет).
# --isolated — профиль в памяти, чтобы параллельные воркеры не блокировали друг друга.
ENTRYPOINT ["playwright-mcp", "--port", "8931", "--host", "0.0.0.0", "--allowed-hosts", "*", "--browser", "chromium", "--isolated"]
