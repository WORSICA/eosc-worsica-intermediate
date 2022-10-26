import os
from . import settings, settings_sensitive

from celery import Celery
from worsica_api import logger

worsica_logger = logger.init_logger('WORSICA-Intermediate.Celery', settings.LOG_PATH)

worsica_logger.info('===================')
worsica_logger.info("THIS INSTANCE IS RUNNING IN PRODUCTION MODE")
worsica_logger.info('===================')

worsica_logger.info("-> kickin' Celery")
# set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'worsica_web_intermediate.settings')

app = Celery(settings_sensitive.CELERY_NAME, broker=settings_sensitive.getCeleryBroker(settings.IS_ON_INCD))

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    worsica_logger.info(f'Request: {self.request!r}')
