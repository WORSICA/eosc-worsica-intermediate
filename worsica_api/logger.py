'''
Worsica Logger
'''

import logging
import logging.handlers
import logging.config

def init_logger(name, addr):
	LOGGING = {
		'version': 1,
		'disable_existing_loggers': False,
		'handlers': {
			'console': {
				'class': 'logging.StreamHandler',
			},
		},
		'root': {
			'handlers': ['console'],
			'level': 'DEBUG',
		},
	}
	logging.config.dictConfig(LOGGING)
	worsica_logger = logging.getLogger(name)
	worsica_logger.setLevel(logging.DEBUG)
	return worsica_logger
