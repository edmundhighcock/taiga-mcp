from .config import *  # noqa: F401,F403

DEBUG = False
CELERY_ENABLED = False
EVENTS_PUSH_BACKEND = "taiga.events.backends.postgresql.EventsPushBackend"
EVENTS_PUSH_BACKEND_OPTIONS = {}
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {  # noqa: F405
    k: None for k in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]  # noqa: F405
}
PUBLIC_REGISTER_ENABLED = False
