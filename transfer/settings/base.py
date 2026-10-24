from dotenv import load_dotenv
from os import environ
from pathlib import Path


# Path Settings

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / '.env')


def env(key, default=None):
    return environ.get(key, default)


def env_list(key, default=''):
    return [item.strip() for item in env(key, default).split(',') if item.strip()]


def env_bool(key, default=False):
    return env(key, str(default)).lower() in ('1', 'true', 'yes')


# Security Settings

SECRET_KEY = env('SECRET_KEY')
JWT_SECRET = env('JWT_SECRET', SECRET_KEY)
JWT_ALGORITHM = 'HS256'
JWT_EXPIRE_DAYS = 14
FERNET_KEY = env('FERNET_KEY')
CODE_EXPIRE_SECONDS = 180
MESSAGE_UPDATE_WINDOW_HOURS = 1
MESSAGE_FILE_MAX_SIZE = 2 * 1024 * 1024

DEBUG = False
ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', '127.0.0.1,localhost')
CORS_ALLOWED_ORIGINS = env_list('CORS_ALLOWED_ORIGINS')
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS')


# Transfer Rules

# A driver is busy from (pickup_time - buffer) to (end_time + buffer)
DRIVER_BUFFER_MINUTES = 30
# Customer cancellation: [{hours, rate}] sorted by hours. The first rule whose
# hours is greater than the remaining time to pickup sets the share kept.
# [] means always full refund.
CANCEL_PENALTY_RULES = []


# Application Definition

AUTH_USER_MODEL = 'core.User'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
ROOT_URLCONF = 'transfer.urls'
ASGI_APPLICATION = 'transfer.asgi.application'
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

INSTALLED_APPS = [
    'daphne',
    'modeltranslation',
    'django.contrib.auth',
    'django.contrib.admin',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'strawberry_django',
    'channels',
    'health_check',
    'storages',
    'fcm_django',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'core.middlewares.AdminLocaleMiddleware',
]

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]


# Databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env('DB_NAME', 'transfer'),
        'USER': env('DB_USER', ''),
        'PASSWORD': env('DB_PASSWORD', ''),
        'HOST': env('DB_HOST', 'localhost'),
        'PORT': env('DB_PORT', '5432'),
        'OPTIONS': {'sslmode': env('DB_SSLMODE', 'prefer')},
    }
}


# Channels

REDIS_URL = env('REDIS_URL', 'redis://localhost:6379/0')

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {'hosts': [REDIS_URL]},
    }
}


# Payment Service (PSP microservice)

PSP_MS_URL = env('PSP_MS_URL', '')
MS_ACCESS_TOKEN = env('MS_ACCESS_TOKEN', '')
# Country code the microservice selects the PSP with
PSP_STATE = env('PSP_STATE', 'TR')


# Celery

CELERY_BROKER_URL = env('CELERY_BROKER_URL', 'redis://localhost:6379/1')
CELERY_TASK_ALWAYS_EAGER = False


# Email

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = env('EMAIL_HOST', '')
EMAIL_PORT = int(env('EMAIL_PORT', '587'))
EMAIL_HOST_USER = env('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', True)
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', 'no-reply@transfer.com')


# Push Notifications (FCM). Disabled when no credentials file is given.

FIREBASE_CREDENTIALS = env('FIREBASE_CREDENTIALS', '')
FCM_ENABLED = bool(FIREBASE_CREDENTIALS)
FCM_DJANGO_SETTINGS = {
    'ONE_DEVICE_PER_USER': False,
    'DELETE_INACTIVE_DEVICES': True,
}

if FCM_ENABLED:
    from firebase_admin import credentials, initialize_app
    FIREBASE_APP = initialize_app(credentials.Certificate(FIREBASE_CREDENTIALS))


# Password Validation

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization

LANGUAGE_CODE = 'en'
TIME_ZONE = 'Europe/Istanbul'
USE_I18N = True
USE_TZ = True

LANGUAGES = (
    ('en', 'English'),
    ('tr', 'Turkish'),
)

LOCALE_PATHS = [BASE_DIR / 'locale']
MODELTRANSLATION_DEFAULT_LANGUAGE = 'en'
MODELTRANSLATION_FALLBACK_LANGUAGES = ('en',)


# Static & Media Files

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'static'
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

USE_S3 = env_bool('USE_S3', False)

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}

if USE_S3:
    STORAGES['default'] = {
        'BACKEND': 'storages.backends.s3.S3Storage',
        'OPTIONS': {
            'bucket_name': env('AWS_STORAGE_BUCKET_NAME'),
            'region_name': env('AWS_S3_REGION_NAME'),
            'access_key': env('AWS_ACCESS_KEY_ID'),
            'secret_key': env('AWS_SECRET_ACCESS_KEY'),
            'custom_domain': env('AWS_S3_CUSTOM_DOMAIN') or None,
            'file_overwrite': False,
            'querystring_auth': env_bool('AWS_QUERYSTRING_AUTH', False),
        },
    }


# Strawberry Django

STRAWBERRY_DJANGO = {
    'FIELD_DESCRIPTION_FROM_HELP_TEXT': True,
    'TYPE_DESCRIPTION_FROM_MODEL_DOCSTRING': True,
    'MUTATIONS_DEFAULT_ARGUMENT_NAME': 'data',
    'MUTATIONS_DEFAULT_HANDLE_ERRORS': False,
    'GENERATE_ENUMS_FROM_CHOICES': False,
    'MAP_AUTO_ID_AS_GLOBAL_ID': False,
    'DEFAULT_PK_FIELD_NAME': 'id',
    'PAGINATION_DEFAULT_LIMIT': 100,
    'PAGINATION_MAX_LIMIT': 100,
}


# Logging

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'console': {
            'format': '%(asctime)s %(name)-12s %(levelname)-8s %(message)s'
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'console'
        },
    },
    'loggers': {
        'django': {
            'level': 'INFO',
            'handlers': ['console'],
            'propagate': True,
        },
        'django.db.backends': {
            'level': 'WARNING',
            'handlers': ['console'],
            'propagate': False,
        },
    },
}
