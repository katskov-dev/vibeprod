FROM mcr.microsoft.com/playwright:latest

RUN npm install -g @playwright/mcp@latest \
 && playwright-mcp install-browser chrome-for-testing

# files MCP: инструменты файлов проекта (MinIO). Живёт в том же контейнере,
# чтобы видеть скриншоты playwright из /vibeprod-shots.
WORKDIR /files-mcp
COPY files-mcp/package.json ./
RUN npm install --omit=dev
COPY files-mcp/server.mjs ./

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8931 8932

HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/8931' || exit 1

# playwright-mcp:
# --allowed-hosts '*' — сеть внутренняя (порт на хост не публикуется),
# воркеры обращаются по имени контейнера vibeprod-playwright:8931.
# --browser chromium — сборка chrome-for-testing (Google Chrome в образе нет).
# --isolated — профиль в памяти, чтобы параллельные воркеры не блокировали друг друга.
# --output-dir /vibeprod-shots — скриншоты по умолчанию рядом с files MCP.
ENTRYPOINT ["/entrypoint.sh"]
