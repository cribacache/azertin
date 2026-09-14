#!/bin/sh
set -e

# Con min/max-instances=1 (recomendado para este servicio, ver README de
# despliegue) esto corre una sola vez por arranque, sin condicion de carrera.
# Si en el futuro se escala a mas de una instancia, mover esto a un Cloud Run
# Job aparte en vez de correrlo en cada arranque de contenedor.
python manage.py migrate --noinput

# La tabla de CACHES (DatabaseCache, config/settings.py) no la crea `migrate`:
# necesita este comando aparte. Falla si la tabla ya existe, por eso el
# `|| true` -no hay forma nativa de pedirle "si no existe" a este comando.
python manage.py createcachetable || true

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:${PORT:-8080} \
    --workers 3 --threads 2 --timeout 60
