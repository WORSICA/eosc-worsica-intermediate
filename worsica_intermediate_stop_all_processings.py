#!/usr/bin/python3
# -*- coding: utf-8 -*-


"""
worsica script for stopping all running job submissions (due to a celery restart)
Author: rjmartins

This script is used for stopping all running processings on JobSubmissions.

This script can be run manually.

"""

import os
import sys
import traceback

if __name__ == '__main__':
    import django
    print(os.getcwd())
    sys.path.append(os.getcwd())
    os.environ['DJANGO_SETTINGS_MODULE'] = 'worsica_web_intermediate.settings'
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "worsica_web_intermediate.settings")

    django.setup()
    from worsica_api import tasks
    import worsica_api.models as worsica_api_models

    try:
        # job submissions
        print('----Job submissions-----')
        job_submissions = worsica_api_models.JobSubmission.objects.filter(
            isVisible=True,
            state__in=['downloading', 'download-waiting-lta', 'converting', 'download-timeout-retry', 'resampling',
                       'merging', 'generating-virtual', 'interpolating-products', 'processing', 'generating-average',
                       'storing', 'dataverse-uploading'])
        print('[worsica_intermediate_stop]: Searching for running job submissions...')
        print('[worsica_intermediate_stop]: Found ' + str(len(job_submissions)) + '...')
        for js in job_submissions:
            try:
                print('[worsica_intermediate_stop]: Stopping ' + js.name + '(id=' + str(js.id) + ', state=' + js.state + ')')
                tasks.taskStopProcessing.apply_async(args=[js.aos_id, js.id, None])
                print('[worsica_intermediate_stop]: Stopped!')
            except Exception as e:
                print('[worsica_intermediate_stop]: Failed stopping!')
                pass
        print('[worsica_intermediate_stop]: Done!')

        # leak detections
        print('----Leak detections-----')
        job_submissions = worsica_api_models.JobSubmission.objects.filter(
            isVisible=True,
            state__in=['success'])
        for js in job_submissions:
            try:
                leak_detections = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(
                    jobSubmission=js,
                    processState__in=['downloading', 'download-waiting-lta', 'download-timeout-retry', 'converting', 'resampling',
                                      'merging', 'processing', 'calculating-diff', 'storing-calculated-diff', 'calculating-leak',
                                      'storing-calculated-leak', 'generating-mask-leak', 'determinating-leak', 'storing-determinated-leak'])
                print('[worsica_intermediate_stop]: Searching for running leak detections on ' + js.name + '(id=' + str(js.id) + ', state=' + js.state + ')')
                print('[worsica_intermediate_stop]: Found ' + str(len(leak_detections)) + '...')
                for leak_detection in leak_detections:
                    try:
                        print('[worsica_intermediate_stop]: Stopping ' + leak_detection.name + '(id=' + str(leak_detection.id) + ', state=' + leak_detection.processState + ')')
                        tasks.taskStopProcessingLeakDetection.apply_async(args=[js.id, leak_detection.id, None])
                        print('[worsica_intermediate_stop]: Stopped!')
                    except Exception as e:
                        print('[worsica_intermediate_stop]: Failed stopping leak detection! ' + str(e))
                        pass
            except Exception as e:
                print('[worsica_intermediate_stop]: Failed! ' + str(e))
                pass
        print('[worsica_intermediate_stop]: Done!')
        exit(0)
    except Exception as e:
        print(traceback.format_exc())
        print('[worsica_intermediate_stop]: Failed!')
        exit(1)
