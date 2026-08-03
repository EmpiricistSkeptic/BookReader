import os
from datetime import timedelta
from pathlib import Path
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
import dj_database_url

from dotenv import load_dotenv

load_dotenv()

SENTRY_DSN = os.getenv("SENTRY_DSN", "")
SENTRY_ENV = os.getenv("SENTRY_ENV", "development")

if SENTRY_DSN:

    sentry_sdk.init(
        dsn=SENTRY_DSN,

        integrations=[
            DjangoIntegration(),
            LoggingIntegration(level=None, event_level="ERROR"),
        ],

        traces_sample_rate=0.2,
        send_default_pii=True,
        environment=SENTRY_ENV,
        release="bookreader@1.0.0",
    )


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("SECRET_KEY", "changeme")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"

ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",")

FILE_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024

GOOGLE_OAUTH2_CLIENT_ID = os.getenv("GOOGLE_OAUTH2_CLIENT_ID")
GOOGLE_OAUTH2_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH2_CLIENT_SECRET")

DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")
DEEPL_BASE_URL = os.getenv("DEEPL_BASE_URL")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPEN_AI_BASE_URL = os.getenv("OPEN_AI_BASE_URL")

MICROSOFT_TRANSLATOR_KEY = os.getenv("MICROSOFT_TRANSLATOR_KEY")
MICROSOFT_TRANSLATOR_REGION = os.getenv("MICROSOFT_TRANSLATOR_REGION")
MICROSOFT_TRANSLATOR_BASE_URL = os.getenv("MICROSOFT_TRANSLATOR_BASE_URL")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL")

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_STORAGE_BUCKET_NAME = os.getenv("AWS_STORAGE_BUCKET_NAME")
AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL")


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_filters",
    "rest_framework",
    "rest_framework_extensions",
    "rest_framework_simplejwt",
    "reader",
    "corsheaders",
    "drf_spectacular",
    "storages",
]


REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "bookreader_core.exceptions.custom_exception_handler",
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "50/hour",
        "user": "1000/hour",
        "translation": "100/hour",
    },
}

REST_FRAMEWORK_EXTENSIONS = {
    "DEFAULT_USE_CACHE": "default",
    "DEFAULT_CACHE_RESPONSE_TIMEOUT": 60 * 10,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "My api",
    "DESCRIPTION": "Documentation for the mobile app",
    "VERSION": "1.0.0",
}

REDIS_URL = os.getenv("REDIS_URL")

if os.getenv("REDIS_URL"):
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            },
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.dummy.DummyCache",
        }
    }


SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "VERIFYING_KEY": None,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}


CORS_ALLOW_ALL_ORIGINS = True

CORS_ALLOW_CREDENTIALS = True


MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "bookreader_core.middleware.request_context.RequestContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "bookreader_core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "bookreader_core.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

#DATABASES = {
    #"default": {
        #"ENGINE": "django.db.backends.postgresql",
        #"HOST": os.getenv("DB_HOST", "db"),
        #"PORT": os.getenv("DB_PORT", 5432),
        #"NAME": os.getenv("DB_NAME", "postgres"),
        #"USER": os.getenv("DB_USER", "postgres"),
        #"PASSWORD": os.getenv("DB_PASSWORD", "postgres"),
    #}
#}

DATABASES = {
    "default": dj_database_url.config(
        default=os.getenv(
            "DATABASE_URL",
            "postgres://postgres:postgres@db:5432/postgres",
        )
    )
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

USE_R2 = all([
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    AWS_STORAGE_BUCKET_NAME,
    AWS_S3_ENDPOINT_URL,
])

if USE_R2:
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "access_key": AWS_ACCESS_KEY_ID,
                "secret_key": AWS_SECRET_ACCESS_KEY,
                "bucket_name": AWS_STORAGE_BUCKET_NAME,
                "endpoint_url": AWS_S3_ENDPOINT_URL,
                "region_name": "auto",
                "default_acl": None,
                "file_overwrite": False,
            },
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if DEBUG:
    reader_handlers = ["reader_file", "console"]
    translation_handlers = ["translation_file", "console"]
    ai_handlers = ["ai_file", "console"]
else:
    reader_handlers = ["console"]
    translation_handlers = ["console"]
    ai_handlers = ["console"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_context": {
            "()": "bookreader_core.logging.filters.RequestContextFilter",
        },
    },
    "formatters": {
        "verbose": {
            "format": "%(asctime)s [%(levelname)s] %(name)s [%(request_id)s %(user_id)s] %(message)s"
        },
        "file_json": {
            "format": '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s",'
                      '"request_id":"%(request_id)s","user_id":"%(user_id)s","msg":"%(message)s"}'
        },
    },
    "handlers": {
        "reader_file": {
            "level": "INFO",
            "class": "logging.FileHandler",
            "filename": "reader.log",
            "formatter": "file_json",
            "filters": ["request_context"],
        },
        "translation_file": {
            "level": "INFO",
            "class": "logging.FileHandler",
            "filename": "translation.log",
            "formatter": "file_json",
            "filters": ["request_context"],
        },
        "ai_file": {
            "level": "INFO",
            "class": "logging.FileHandler",
            "filename": "ai_service.log",
            "formatter": "file_json",
            "filters": ["request_context"],
        },
        "console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": "verbose",
            "filters": ["request_context"],
        },
    },
    "loggers": {
        "reader": {
        "handlers": reader_handlers,
        "level": "INFO",
        "propagate": False,
        },
        "translation": {
            "handlers": translation_handlers,
            "level": "INFO",
            "propagate": False,
        },
        "ai_service": {
            "handlers": ai_handlers,
            "level": "INFO",
            "propagate": False,
        },
        "reader.openapi_extensions": {
            "handlers": ["console"], 
            "level": "INFO",
            "propagate": False,
        },
    }
}
