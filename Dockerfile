FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# libpq5: libreria de conexion a Postgres que necesita psycopg en runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

# No necesita DB ni secretos: solo junta static/ en STATIC_ROOT.
RUN python manage.py collectstatic --noinput

# Cloud Run pasa el puerto real en $PORT (8080 por defecto); no fijarlo a 8000.
CMD ["./entrypoint.sh"]
