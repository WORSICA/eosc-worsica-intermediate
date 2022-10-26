#!/usr/bin/python3
# -*- coding: utf-8 -*-


"""
worsica script for automatic imageset cleanup
Author: rjmartins

This script is used for cleaning (old and unused) imagesets from the repository_images folder,
in order to free up some space on disk.

This script can be run manually, but it's supposed to be run in celery, at midnight of each day.

"""

import os
import sys

if __name__ == '__main__':
	import django
	print(os.getcwd())
	sys.path.append(os.getcwd())
	os.environ['DJANGO_SETTINGS_MODULE'] = 'worsica_web_intermediate.settings'
	os.environ.setdefault("DJANGO_SETTINGS_MODULE", "worsica_web_intermediate.settings")

	django.setup()
	import worsica_api.processing_cleanup as proc_cleanup
	exit(proc_cleanup.check())
