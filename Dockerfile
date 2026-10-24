FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt --no-cache-dir
COPY . /app

ARG DJANGO_SETTINGS_MODULE=transfer.settings.prod
ENV DJANGO_SETTINGS_MODULE=$DJANGO_SETTINGS_MODULE

RUN chmod +x ./entrypoint.sh
EXPOSE 80

ENTRYPOINT ["/bin/bash", "./entrypoint.sh"]
