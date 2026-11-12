"""Test settings configuration for Campus Nexus.

Optimized for fast, isolated, deterministic test suite execution.
- In-memory database & cache
- In-memory channel layer (no external Redis dependency required for unit tests)
- Fast MD5 password hasher (10x faster unit tests)
- In-memory locmem email backend
- DEBUG is False to verify production-like view branching
"""

import tempfile
import atexit
import shutil

from .base import *

DEBUG = False
TESTING = True

# Direct uploaded test media to isolated temporary directory and purge at exit
_TEST_MEDIA_DIR = tempfile.mkdtemp(prefix='campus_test_media_')
MEDIA_ROOT = Path(_TEST_MEDIA_DIR)
atexit.register(lambda: shutil.rmtree(_TEST_MEDIA_DIR, ignore_errors=True))

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

DATABASES = {
    'default': env.db('TEST_DATABASE_URL', default='sqlite:///:memory:'),
    'guest_demo': env.db('TEST_GUEST_DEMO_DATABASE_URL', default='sqlite:///:memory:'),
}

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'campus-nexus-test-cache',
    }
}

SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0


VAPID_PUBLIC_KEY = 'test_vapid_public_key_mock_12345678901234567890'
VAPID_PRIVATE_KEY = 'test_vapid_private_key_mock_12345678901234567890'

# Fail-safe guard: never allow automated tests to mutate live PostgreSQL or production MEDIA_ROOT
if os.environ.get('ALLOW_POSTGRES_TESTS', '').lower() not in ('1', 'true', 'yes'):
    for _db_alias, _db_cfg in DATABASES.items():
        if 'postgres' in str(_db_cfg.get('ENGINE', '')).lower():
            raise RuntimeError(
                f"Blocked test execution against PostgreSQL database alias '{_db_alias}'. "
                "Set ALLOW_POSTGRES_TESTS=1 only when explicitly targeting a dedicated test database."
            )
    if Path(MEDIA_ROOT).resolve() == (BASE_DIR / 'media').resolve():
        raise RuntimeError("Blocked test execution against production MEDIA_ROOT.")
