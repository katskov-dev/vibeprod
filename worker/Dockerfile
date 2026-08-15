# Образ воркера: opencode + базовый набор инструментов для кодинг-агентов.
# Версия запинена (не latest) — репродуцируемые деплои.
FROM ghcr.io/anomalyco/opencode:1.0.196@sha256:c2b4d1f55f181c797e78f898872aae8608deab3ecf720e4db6cec79df1564296

# В базе уже есть: wget, ripgrep (rg), unzip, bun (JS/TS), ssh.
# Докладываем лёгкое и часто нужное агентам:
#   python3 + pip — скрипты и установка пакетов;
#   curl, jq     — HTTP и JSON;
#   bash, make   — скрипты и сборка;
#   zip, openssl, file — архивы, TLS/хеши, типы файлов.
# Компиляторы (build-base) сознательно не включены — они раздувают образ
# (учтите: на Alpine многие python-пакеты без готовых musl-wheels не
# соберутся из исходников; для них есть bun/deno или ставите gcc сами).
RUN apk add --no-cache \
    git openssh-client ca-certificates \
    python3 py3-pip \
    curl jq \
    bash \
    make \
    zip openssl file
