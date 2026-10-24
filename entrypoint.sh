#!/bin/bash
set -e

python manage.py migrate --no-input
python manage.py collectstatic --no-input

# Celery worker: run as a separate container/process
#   celery -A transfer worker -l info
exec daphne -b 0.0.0.0 -p 80 transfer.asgi:application
