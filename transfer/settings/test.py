from transfer.settings.base import *


# Security Settings

DEBUG = False
SECRET_KEY = 'test-secret-key'
JWT_SECRET = 'test-jwt-secret-with-at-least-32-bytes-length'
FERNET_KEY = 'PrfC6-ryvvydMXHK6qp8cCLvYkLu-CaRwE7hmxCd6t4='
PSP_MS_URL = 'https://psp.test'
MS_ACCESS_TOKEN = 'test-ms-access-token'


# Databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test.sqlite3',
    }
}


# Channels / Celery / Email: in-process

CHANNEL_LAYERS = {
    'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}
}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


# Files / Push: local storage, no FCM

USE_S3 = False
STORAGES['default'] = {
    'BACKEND': 'django.core.files.storage.FileSystemStorage',
}
FCM_ENABLED = False
CANCEL_PENALTY_RULES = []
