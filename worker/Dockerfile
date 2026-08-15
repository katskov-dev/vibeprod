# Образ воркера: opencode + базовый набор инструментов для кодинг-агентов.
# Версия запинена (не latest) — репродуцируемые деплои.
# Важно: берите версии с живым каталогом models.dev (в старых вроде 1.0.196
# модели зашиты в образ и не обновляются — «не знает модель» для новых моделей).
FROM ghcr.io/anomalyco/opencode:1.18.18@sha256:f3e00f8e25500150373c817e24b13f2f08e2ccd4cafd53dc3ad4827d47863b6f

# В базе уже есть: wget, ripgrep (rg), unzip, bun (JS/TS), ssh.
# Докладываем лёгкое и часто нужное агентам:
#   python3 + pip — скрипты и установка пакетов;
#   curl, jq     — HTTP и JSON;
#   bash, make   — скрипты и сборка;
#   sshpass      — ssh-доступ с паролем на удалённые серверы;
#   zip, openssl, file — архивы, TLS/хеши, типы файлов.
# Компиляторы (build-base) сознательно не включены — они раздувают образ
# (учтите: на Alpine многие python-пакеты без готовых musl-wheels не
# соберутся из исходников; для них есть bun/deno или ставите gcc сами).
RUN apk add --no-cache \
    git openssh-client ca-certificates \
    python3 py3-pip \
    curl jq \
    bash \
    sshpass \
    make \
    zip openssl file
