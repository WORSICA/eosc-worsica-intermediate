print('load sensitive data')
# Feel free to change this to your needs.
# IS_ON_INCD: Custom boolean flag to distinguish different configs

# django
SECRET_KEY = ''  # your django secret key


def getAllowedHosts(IS_ON_INCD):
    if IS_ON_INCD:
        ALLOWED_HOSTS = ['127.0.0.1', 'localhost', ]
    else:
        ALLOWED_HOSTS = ['127.0.0.1', 'localhost', ]
    return ALLOWED_HOSTS


# celery
# read celery documentation
CELERY_NAME = ''


def getCeleryBroker(IS_ON_INCD):
    if IS_ON_INCD:
        CELERY_BROKER_URL = ''  # URL and port for your worsica intermediate
    else:
        CELERY_BROKER_URL = ''
    return CELERY_BROKER_URL

# custom vars
# WORSICA_FOLDER_PATH indicate full local path where all worsica components are located
# LOG_PATH where to send logs
# VENV_PYTHON_EXECUTABLE if you use venv to run, set the python3 executable, else set blank


def getPaths(IS_ON_INCD):
    if IS_ON_INCD:
        WORSICA_FOLDER_PATH = '/usr/local'
        VENV_PYTHON_EXECUTABLE = 'python3'
        LOG_PATH = '/dev/log'
    else:
        WORSICA_FOLDER_PATH = '/usr/local'
        VENV_PYTHON_EXECUTABLE = 'python3'
        LOG_PATH = '/dev/log'
    return WORSICA_FOLDER_PATH, VENV_PYTHON_EXECUTABLE, LOG_PATH

# DB
# set DB configs according to your needs, read django documentation for more info


def getDatabaseConfigs(DEBUG, IS_ON_INCD):
    if DEBUG:
        DATABASES = {
            'default': {
                'ENGINE': 'django.contrib.gis.db.backends.postgis',
                'NAME': '',
                'USER': '',
                'PASSWORD': '',
                'HOST': '',
                'PORT': '',
            }
        }
    else:
        DATABASES = {
            'default': {
                'ENGINE': 'django.contrib.gis.db.backends.postgis',
                'NAME': '',
                'USER': '',
                'PASSWORD': '',
                'HOST': '',
                'PORT': '',
            }
        }
    return DATABASES


# email
# read django documentnation for more info
# WORSICA_DEFAULT_EMAIL the email address from who is sending the email
# MANAGERS are the list of emails to send (this is applied for user registrations)
EMAIL_HOST = ''
EMAIL_USE_TLS = False
EMAIL_PORT = 25
EMAIL_HOST_USER = ''
EMAIL_HOST_PASSWORD = ''
WORSICA_DEFAULT_EMAIL = ''


def getEmailManagers(DEBUG):
    if DEBUG:
        MANAGERS = ('', )
    else:
        MANAGERS = ('', )
    return MANAGERS


# GRID
# flag to activate GRID or not
ENABLE_GRID = False
