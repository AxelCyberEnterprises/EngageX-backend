"""
Django settings for EngageX project.

Generated by 'django-admin startproject' using Django 4.2.7.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/4.2/ref/settings/
"""

from pathlib import Path
import os
from decouple import config
from dotenv import load_dotenv

load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "django-insecure-wfl*mho^tyaghxhwx4p^2u8)yl#gw+^ub&(=!m#=!x3rrqo1og"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

ALLOWED_HOSTS = [".elasticbeanstalk.com", "api.engagexai.io", "*"]

# Application definition

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Apps I installed
    "rest_framework",
    "corsheaders",
    "users",
    "payments",
    "practice_sessions",
    "streaming",
    # Apps for authentication
    "djoser",
    "rest_framework.authtoken",
    # API Documentation
    "drf_yasg",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# REST Framework settings
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PARSER_CLASSES": (
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.MultiPartParser",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

DJOSER = {
    "LOGIN_FIELD": "email",
    "USER_CREATE_PASSWORD_RETYPE": True,
    "SEND_ACTIVATION_EMAIL": True,  # Make sure this is enabled
    "SEND_CONFIRMATION_EMAIL": False,
    "PASSWORD_RESET_CONFIRM_URL": "password/reset/confirm/{uid}/{token}",
    "ACTIVATION_URL": "activate/{uid}/{token}",  # Activation link URL
    "SERIALIZERS": {
        "user_create": "users.serializers.UserSerializer",
        "user": "users.serializers.UserSerializer",
        "token_create": "users.serializers.CustomTokenCreateSerializer",
    },
}

AUTH_USER_MODEL = "users.CustomUser"

ROOT_URLCONF = "EngageX.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "EngageX.wsgi.application"

ASGI_APPLICATION = "EngageX.asgi.application"

# Socket.IO settings
SOCKETIO = {
    "CORS_ALLOWED_ORIGINS": "*",  # Configure this appropriately in production
    "ASYNC_MODE": "asgi",
}

CORS_ALLOW_ALL_ORIGINS = True
# CORS_ALLOWED_ORIGINS = [
#     "https://www.engagexai.io",
#     "http://localhost:5173",
#     "https://api.engagexai.io",
#     "https://main.d2wwdi7x8g70xe.amplifyapp.com",
# ]

CSRF_TRUSTED_ORIGINS = [
    "https://api.engagexai.io",
]

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = config("EMAIL_HOST")
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = config("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL")
AWS_ACCESS_KEY_ID = config("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = config("AWS_SECRET_ACCESS_KEY")
AWS_SES_REGION = config("AWS_SES_REGION", "us-west-1")

# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.sqlite3',
#         'NAME': BASE_DIR / 'db.sqlite3',
#     }
# }


# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.postgresql',
#         'NAME': config('POSTGRESQL_DATABASE_NAME'),
#         'USER': config('POSTGRESQL_USERNAME'),
#         'PASSWORD': config('POSTGRESQL_PASSWORD'),
#         'HOST': config('POSTGRESQL_SERVER_NAME'),
#         'PORT': config('PORT', default='5432'),
#         'OPTIONS': {
#             'sslmode': 'require',
#         },
#     }
# }

if "RDS_HOSTNAME" in os.environ:
    # Production settings (AWS RDS MySQL)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["POSTGRESQL_DATABASE_NAME"],
            "USER": os.environ["POSTGRESQL_USERNAME"],
            "PASSWORD": os.environ["POSTGRESQL_PASSWORD"],
            "HOST": os.environ["POSTGRESQL_SERVER_NAME"],
            "PORT": os.environ.get("POSTGRESS_PORT", default="5432"),
        }
    }
    OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
    DEEPGRAM_API_KEY = os.environ["DEEPGRAM_API_KEY"]

    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ("EMAIL_HOST")
    EMAIL_PORT = 587
    EMAIL_USE_TLS = True
    EMAIL_HOST_USER = os.environ("EMAIL_HOST_USER")
    EMAIL_HOST_PASSWORD = os.environ("EMAIL_HOST_PASSWORD")
    DEFAULT_FROM_EMAIL = os.environ("DEFAULT_FROM_EMAIL")

    AWS_ACCESS_KEY_ID = os.environ("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.environ("AWS_SECRET_ACCESS_KEY")
    AWS_SES_REGION = os.environ("AWS_SES_REGION", "us-west-1")

    INTUIT_CLIENT_ID = os.environ('INTUIT_CLIENT_ID')
    INTUIT_CLIENT_SECRET = os.environ('INTUIT_CLIENT_SECRET')
    INTUIT_REDIRECT_URI = os.environ('NEW_INTUIT_REDIRECT_URI')
    INTUIT_ENVIRONMENT = 'production'
    INTUIT_WEBHOOK_VERIFIER_TOKEN = os.environ('INTUIT_VERIFIER_TOKEN')

    INTUIT_API_BASE_URL = 'https://quickbooks.api.intuit.com' if INTUIT_ENVIRONMENT == 'production' else 'https://sandbox-quickbooks.api.intuit.com'


else:
    # Local development settings (PostgreSQL)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": config("POSTGRESQL_DATABASE_NAME"),
            "USER": config("POSTGRESQL_USERNAME"),
            "PASSWORD": config("POSTGRESQL_PASSWORD"),
            "HOST": config("POSTGRESQL_SERVER_NAME"),
            "PORT": config("PORT", default="5432"),
            "OPTIONS": {
                "sslmode": "require",
            },
        }
    }

    OPENAI_API_KEY = config("OPENAI_API_KEY")
    DEEPGRAM_API_KEY = os.environ["DEEPGRAM_API_KEY"]

    INTUIT_CLIENT_ID = os.environ['INTUIT_CLIENT_ID']
    INTUIT_CLIENT_SECRET = os.environ['INTUIT_CLIENT_SECRET']
    INTUIT_REDIRECT_URI = os.environ['NEW_INTUIT_REDIRECT_URI']
    INTUIT_ENVIRONMENT = 'production'
    INTUIT_WEBHOOK_VERIFIER_TOKEN = os.environ['INTUIT_VERIFIER_TOKEN']

    INTUIT_API_BASE_URL = 'https://quickbooks.api.intuit.com' if INTUIT_ENVIRONMENT == 'production' else 'https://sandbox-quickbooks.api.intuit.com'

# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

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

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",  # Default authentication
]

# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_TOKEN_FOR_WEBSOCKET = os.environ["AUTH_TOKEN_FOR_WEBSOCKET"]

USE_S3 = os.environ["USE_S3"] == "True"
AWS_STORAGE_BUCKET_NAME = os.environ["AWS_STORAGE_BUCKET_NAME"]
# # aws setting
if USE_S3:

    AWS_S3_REGION_NAME = os.environ["AWS_S3_REGION_NAME"]
    AWS_S3_CUSTOM_DOMAIN = f"{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com"

    AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")

    # s3 media settings
    MEDIA_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/"
    STATIC_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/static/"

    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
            "OPTIONS": {
                "bucket_name": AWS_STORAGE_BUCKET_NAME,
            },
        },
        "staticfiles": {
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",  # For static files
            "OPTIONS": {
                "bucket_name": AWS_STORAGE_BUCKET_NAME,
                "location": "static/",  # Stores static files in 'static/' folder in S3
            },
        },
        "ProfilePicStorage": {
            "BACKEND": "users.storages_backends.ProfilePicStorage",
        },
        "OPTIONS": {
            "bucket_name": AWS_STORAGE_BUCKET_NAME,
            "location": "profile-pic/",  # Stores static files in 'static/' folder in S3
        },
        "SlidesStorage": {
            "BACKEND": "users.storages_backends.SlidesStorage",
            "OPTIONS": {
                "bucket_name": AWS_STORAGE_BUCKET_NAME,
                # "location": "slides/",
            },
        },
        "StaticVideosStorage": {
            "BACKEND": "users.storages_backends.StaticVideosStorage",
        },
        "UserVideosStorage": {
            "BACKEND": "users.storages_backends.StaticVideosStorage",
        },
    }


else:
    # Static files (CSS, JavaScript, Images)
    # https://docs.djangoproject.com/en/4.2/howto/static-files/

    STATIC_URL = "static/"
    STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")
    MEDIA_URL = "/media/"
    MEDIA_ROOT = os.path.join(BASE_DIR, "media")

# PUBLIC_MEDIA_LOCATION = "media"
# MEDIA_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/{AWS_STORAGE_BUCKET_NAME}/"
# DEFAULT_FILE_STORAGE = "hello_django.storage_backends.PublicMediaStorage"


# Media files settings
# MEDIA_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/media/"
# STATIC_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/static/"
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}

CORS_ALLOW_METHODS = [
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
]
# settings.py

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
        "file": {
            "level": "ERROR",
            "class": "logging.FileHandler",
            "filename": "error.log",
        },
    },
    "loggers": {
        "streaming.consumers": {
            "handlers": ["console", "file"],
            "level": "ERROR",
            "propagate": False,
        },
        # Add other loggers as needed
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}
