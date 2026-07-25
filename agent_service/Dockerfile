# Build from the repo root: `docker build -f agent_service/Dockerfile .`
# (needs repo-root context so it can COPY the shared common/ package in.)
FROM python:3.11-slim

WORKDIR /app

COPY common/ ./common/
COPY agent_service/ ./agent_service/

# Deliberately `./common` with no extras — agent_service never imports
# seo_workbook_common.output, so it skips WeasyPrint's native deps entirely
# and stays a much lighter image than mcp_server's.
RUN pip install --no-cache-dir ./common ./agent_service

ENV PYTHONUNBUFFERED=1
ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "seo_workbook_agent.main"]
