#!/usr/bin/python3
# -*- coding: utf-8 -*-


"""
worsica script for sending metrics by email
Author: rjmartins

Script to send emails.

Args:
>1) MONTHYEAR is the month and year of the metrics to be sent by email

Usage: ./worsica_sendmail_metrics.py  [MONTHYEAR]
"""
import os
import sys


def sendMetricsByEmail(monthYearSplit):
    try:
        email = EmailMessage(
            '[WORSICA] Metrics for ' + str(monthYearSplit[0] + '-' + monthYearSplit[1]),
            'Hello, \n Dont forget to provide the monthly metrics. \n\n Link to download the Metrics for ' + str(monthYearSplit[0] + '-' + monthYearSplit[1]) + ': \n\n The WORSICA team',
            'worsica@lnec.pt',
            ['worsica@lnec.pt'],
        )
        print(monthYear)
        email.send(fail_silently=False)
    except Exception as e:
        print(e)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: ./worsica_sendmail_metrics.py  [MONTHYEAR]")
        exit(1)
    else:
        monthYear = sys.argv[1]
        monthYearSplit = monthYear.split('-')
        print(monthYearSplit)
        if (len(monthYearSplit) != 2):
            print('ERROR: Date must be YYYY-MM format.')
            exit(1)
        elif (len(monthYearSplit[0]) != 4 or len(monthYearSplit[1]) != 2):
            print('ERROR: ' + sys.argv[1] + ' some parsed date is not of correct size')
            exit(1)
        elif (int(monthYearSplit[0]) not in range(2000, 9999) or int(monthYearSplit[1]) not in range(1, 12)):
            print('ERROR: ' + sys.argv[1] + ' this date is not between 2000-01 and 9999-12')
            exit(1)
        else:
            import django

            sys.path.append(os.getcwd())
            os.environ['DJANGO_SETTINGS_MODULE'] = 'worsica_web.settings'
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "worsica_web.settings")

            django.setup()

            from django.core.mail import EmailMessage

            sendMetricsByEmail(monthYearSplit)
            exit(0)
