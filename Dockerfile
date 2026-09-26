FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY orivan_engine ./orivan_engine
CMD ["python", "-m", "orivan_engine"]
