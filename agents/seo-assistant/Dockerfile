# Build from the repo root: `docker build -f agents/seo-assistant/Dockerfile .`
# (needs repo-root context — this is the only component that pulls in
# *three* sibling packages: shared/, seo-workbook-agent/, and
# seo-testing-agent/, since its sub-agents run in this same process.)
FROM python:3.11-slim

WORKDIR /app

COPY shared/ ./common/
COPY agents/seo-workbook-agent/ ./seo-workbook-agent/
COPY agents/seo-testing-agent/ ./seo-testing-agent/
COPY agents/seo-assistant/ ./seo-assistant/

# One install command so all four packages land in the same site-packages
# and cross-import correctly (seo-assistant imports from the other three).
RUN pip install --no-cache-dir ./common ./seo-workbook-agent ./seo-testing-agent ./seo-assistant

ENV PYTHONUNBUFFERED=1
ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "seo_assistant.main"]
