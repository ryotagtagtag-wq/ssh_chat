# ---- ビルドステージ ----
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ---- 実行ステージ ----
FROM python:3.12-slim AS runner

# セキュリティ: 非rootユーザーで実行
RUN groupadd --gid 1001 appgroup \
 && useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# インストール済みパッケージをビルドステージからコピー
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# アプリコードをコピー
COPY --chown=appuser:appgroup ssh_chat_server.py .

# データ永続化ディレクトリ
RUN mkdir -p /data && chown appuser:appgroup /data

USER appuser

EXPOSE 10000

# ヘルスチェック: /health エンドポイントに HTTP GET
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:10000/health')" || exit 1

CMD ["python", "-u", "ssh_chat_server.py"]
