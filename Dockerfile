FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	FLASK_ENV=production \
	PORT=8080

WORKDIR /app

RUN apt-get update \
	&& apt-get install -y --no-install-recommends default-jdk-headless \
	&& rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

RUN python -m pip install --upgrade pip \
	&& pip install --no-cache-dir -r requirements.txt

COPY . /app

EXPOSE 8080

CMD ["sh", "-c", "gunicorn -w ${GUNICORN_WORKERS:-2} -b 0.0.0.0:${PORT:-8080} run:app"]
