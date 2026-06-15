FROM python:3.11-slim
WORKDIR /app
COPY . /app
RUN python -m pip install --upgrade pip && pip install -r requirements.txt
ENV FLASK_ENV=production
EXPOSE 5000
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "run:app"]
