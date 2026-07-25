# Build from the repo root: `docker build -f mcp_server/Dockerfile .`
# (needs repo-root context so it can COPY the shared common/ package in.)
FROM python:3.11-slim

WORKDIR /app

COPY common/ ./common/
COPY mcp_server/ ./mcp_server/
COPY data/ ./data/

RUN pip install --no-cache-dir "./common[output]" ./mcp_server

ENV PYTHONUNBUFFERED=1
ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "seo_workbook_mcp.app"]
