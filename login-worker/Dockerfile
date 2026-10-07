FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /srv/worker

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "playwright_server:app", "--host", "0.0.0.0", "--port", "8000"]
