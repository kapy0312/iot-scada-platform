# 雲端 MCP 的 Lambda 容器映像檔
# 在 backend 資料夾建置（context 是 backend）：
#   docker build -f mcp_cloud/Dockerfile -t iot-scada-mcp .
# Lambda Web Adapter：把 Lambda 的呼叫轉成一般 HTTP，程式照一般網頁伺服器寫就好（跟 rag-platform 同一套）
FROM public.ecr.aws/docker/library/python:3.12-slim

COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:1.1.0 /lambda-adapter /opt/extensions/lambda-adapter

ENV PORT=8080 \
    HOST=0.0.0.0 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY mcp_cloud/requirements-lambda.txt .
RUN pip install --no-cache-dir -r requirements-lambda.txt

# 只放雲端 MCP 用得到的程式（不含 PLC、ML 模型、前端）
COPY core/ core/
COPY db/__init__.py db/pool.py db/oee_repo.py db/
COPY rag/embedder.py rag/pgvector_store.py rag/
COPY mcp_tools/ mcp_tools/
COPY mcp_cloud/ mcp_cloud/

CMD ["python", "-m", "mcp_cloud.server"]
