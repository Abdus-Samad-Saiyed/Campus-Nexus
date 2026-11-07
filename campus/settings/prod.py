import sys
import environ

from .base import *  # noqa: F401, F403
from django.core.exceptions import ImproperlyConfigured as DjangoImproperlyConfigured


class ImproperlyConfigured(DjangoImproperlyConfigured, ValueError):
    """Configuration error for production environment."""
    pass

env = environ.Env()

DEBUG = False
IS_BUILD_PHASE = env.bool('DJANGO_BUILD_PHASE', default=False)

# Production MUST never import secrets_local
if 'secrets_local' in globals() or 'campus.settings.secrets_local' in sys.modules:
    raise ImproperlyConfigured("Production settings must not import secrets_local.py")

# --- 1. Secret Key Validation ------------------------------------------------
SECRET_KEY = env('DJANGO_SECRET_KEY', default='')
if IS_BUILD_PHASE and (not SECRET_KEY or SECRET_KEY.startswith('django-insecure-')):
    SECRET_KEY = 'build-phase-dummy-secret-key-for-static-collection-and-checks-50ch!'

_INSECURE_DEFAULTS = {
    'insecure-dev-key-change-me',
    'insecure-dev-only-secret-key-for-local-testing',
    'replace-with-a-long-random-secret-key-at-least-50-chars',
    ''
}
if not IS_BUILD_PHASE and (not SECRET_KEY or SECRET_KEY in _INSECURE_DEFAULTS or SECRET_KEY.startswith('django-insecure-') or len(SECRET_KEY) < 50):
    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY is missing, too short, or using an insecure default. '
        'Set a strong, unique DJANGO_SECRET_KEY environment variable (at least 50 characters) for production.'
    )

# --- 2. Allowed Hosts Validation ---------------------------------------------
ALLOWED_HOSTS = env.list('DJANGO_ALLOWED_HOSTS', default=['build.local'] if IS_BUILD_PHASE else [])
_INSECURE_HOSTS = {'*', 'localhost', '127.0.0.1', 'testserver', ''}
if not IS_BUILD_PHASE:
    if not ALLOWED_HOSTS or any(h.strip() in _INSECURE_HOSTS or h.strip().startswith('.') for h in ALLOWED_HOSTS):
        raise ImproperlyConfigured(
            'DJANGO_ALLOWED_HOSTS must contain only valid production hostnames. '
            'Wildcards (*), localhost, 127.0.0.1, and testserver are forbidden even when mixed.'
        )
valid_hosts = ALLOWED_HOSTS

# --- 3. Production Database (PostgreSQL & SQLite3) ---------------------------
database_url = env('DATABASE_URL', default='')
allow_sqlite_prod = env.bool('DJANGO_ALLOW_SQLITE_PROD', default=False)

if IS_BUILD_PHASE:
    DATABASES['default'] = {'ENGINE': 'django.db.backends.dummy'}
else:
    if not database_url:
        raise ImproperlyConfigured(
            'DATABASE_URL environment variable is required for production (PostgreSQL or SQLite).'
        )
    if database_url.startswith('sqlite') and not allow_sqlite_prod:
        raise ImproperlyConfigured(
            'SQLite is not allowed in production without DJANGO_ALLOW_SQLITE_PROD=True. '
            'Set DJANGO_ALLOW_SQLITE_PROD=True to explicitly enable SQLite in production, '
            'or point DATABASE_URL to PostgreSQL.'
        )
    DATABASES['default'] = env.db('DATABASE_URL')
    if DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3':
        DATABASES['default'].setdefault('OPTIONS', {})
        DATABASES['default']['OPTIONS'].update({
            'timeout': 20,
            'init_command': 'PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON;',
            'transaction_mode': 'IMMEDIATE',
        })

    # Early launch mode flag
    if os.environ.get('CAMPUS_EARLY_LAUNCH_MODE', '').lower() in ('true', '1', 'yes') or env.bool('EARLY_LAUNCH_MODE', default=False):
        EARLY_LAUNCH_MODE = True

# --- 4. Distributed Cache / Channel Layer (Redis or Local) -------------------
# Production strictly retains high-iteration standard PBKDF2 algorithm for credential safety
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
    'django.contrib.auth.hashers.ScryptPasswordHasher',
]
REDIS_URL = env('REDIS_URL', default='redis://dummy:6379/0' if IS_BUILD_PHASE else '')
allow_local_cache = env.bool('DJANGO_ALLOW_LOCAL_CACHE', default=False)

if not REDIS_URL and not IS_BUILD_PHASE:
    if not allow_local_cache:
        raise ImproperlyConfigured(
            'REDIS_URL environment variable is required for production (shared Channels and Cache). '
            'For standalone deployments without Redis, set DJANGO_ALLOW_LOCAL_CACHE=True.'
        )
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        },
    }
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'production-locmem-cache',
        }
    }
else:
    _redis_channel_url = REDIS_URL
    if 'protocol=' not in _redis_channel_url:
        delimiter = '&' if '?' in _redis_channel_url else '?'
        _redis_channel_url = f"{_redis_channel_url}{delimiter}protocol=2"
    if 'socket_timeout=' not in _redis_channel_url:
        delimiter = '&' if '?' in _redis_channel_url else '?'
        _redis_channel_url = f"{_redis_channel_url}{delimiter}socket_timeout=15&socket_connect_timeout=5"
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [_redis_channel_url],
            },
        },
    }
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
        }
    }

# --- 5. Email Configuration --------------------------------------------------
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='build-phase-dummy' if IS_BUILD_PHASE else '')
if not EMAIL_HOST_PASSWORD and not IS_BUILD_PHASE:
    raise ImproperlyConfigured(
        'EMAIL_HOST_PASSWORD must be set in the production environment.'
    )

# --- 6. Reverse Proxy & Security Headers -------------------------------------
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = env.bool('DJANGO_SECURE_SSL_REDIRECT', default=True)
SESSION_COOKIE_SECURE = env.bool('DJANGO_SESSION_COOKIE_SECURE', default=True)
CSRF_COOKIE_SECURE = env.bool('DJANGO_CSRF_COOKIE_SECURE', default=True)
SECURE_HSTS_SECONDS = env.int('DJANGO_SECURE_HSTS_SECONDS', default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool('DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS', default=True)
SECURE_HSTS_PRELOAD = env.bool('DJANGO_SECURE_HSTS_PRELOAD', default=True)

CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])
TRUST_X_FORWARDED_FOR = env.bool('DJANGO_TRUST_X_FORWARDED_FOR', default=False)
TRUSTED_PROXY_IPS = set(env.list('DJANGO_TRUSTED_PROXY_IPS', default=['127.0.0.1']))

# --- 7. Logging with Data Minimization ---------------------------------------
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'channels_server': {
            'format': '%(message)s',
        },
        'production': {
            'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'production',
        },
        'channels_console': {
            'class': 'logging.StreamHandler',
            'formatter': 'channels_server',
        },
    },
    'loggers': {
        'django.channels.server': {
            'handlers': ['channels_console'],
            'level': 'INFO',
            'propagate': False,
        },
        'daphne': {
            'handlers': ['channels_console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
    'root': {
        'handlers': ['console'],
        'level': env('DJANGO_LOG_LEVEL', default='INFO'),
    },
}

# --- 8. Static Storage -------------------------------------------------------
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# --- 9. WebAuthn Passkey Production Configuration ----------------------------
WEBAUTHN_RP_ID = env('WEBAUTHN_RP_ID', default=valid_hosts[0] if valid_hosts else ('build.local' if IS_BUILD_PHASE else ''))
_INSECURE_RP_IDS = {'*', 'localhost', '127.0.0.1', 'testserver', ''}
if (not WEBAUTHN_RP_ID or WEBAUTHN_RP_ID in _INSECURE_RP_IDS) and not IS_BUILD_PHASE:
    raise ImproperlyConfigured('WEBAUTHN_RP_ID must be set to a valid production domain.')

WEBAUTHN_ALLOWED_ORIGINS = env.list(
    'WEBAUTHN_ALLOWED_ORIGINS',
    default=[f'https://{h}' for h in valid_hosts]
)
if not WEBAUTHN_ALLOWED_ORIGINS and not IS_BUILD_PHASE:
    raise ImproperlyConfigured('WEBAUTHN_ALLOWED_ORIGINS must contain valid production origins.')
for origin in WEBAUTHN_ALLOWED_ORIGINS:
    if not origin.startswith('https://') and not IS_BUILD_PHASE:
        raise ImproperlyConfigured(f'WebAuthn origin {origin} must use HTTPS in production.')

# --- 10. CORS (Mobile & React Native Frontend Origins) -----------------------
CORS_ALLOW_ALL_ORIGINS = env.bool('CORS_ALLOW_ALL_ORIGINS', default=False)
CORS_ALLOWED_ORIGINS = env.list(
    'CORS_ALLOWED_ORIGINS',
    default=[f'https://{h}' for h in valid_hosts if not h.startswith('.')]
)
