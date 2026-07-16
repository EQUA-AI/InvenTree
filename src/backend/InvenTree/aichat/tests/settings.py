"""Minimal settings for portable durable chat store tests."""

SECRET_KEY = 'aichat-test-only'
USE_TZ = True
INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'aichat.apps.AIChatConfig',
    'voice.apps.VoiceConfig',
]
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}
MIDDLEWARE = []
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
