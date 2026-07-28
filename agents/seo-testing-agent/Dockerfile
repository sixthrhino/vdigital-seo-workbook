# Build from the repo root: `docker build -f agents/seo-testing-agent/Dockerfile .`
FROM python:3.11-slim

WORKDIR /app

COPY agents/seo-testing-agent/ ./seo-testing-agent/
RUN pip install --no-cache-dir ./seo-testing-agent

ENV PYTHONUNBUFFERED=1
ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "seo_testing_agent.main"]
