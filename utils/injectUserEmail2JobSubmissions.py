#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import requests
import json
# add user_email to older submissions
if __name__ == '__main__':
    import django

    sys.path.append("/usr/local/worsica_web_intermediate")
    os.environ['DJANGO_SETTINGS_MODULE'] = 'worsica_web_intermediate.settings'
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "worsica_web_intermediate.settings")

    django.setup()

    import worsica_api.models as worsica_api_models

    job_submissions = worsica_api_models.JobSubmission.objects.filter(isVisible=True)
    for job_submission in job_submissions:
        job_submission_id = job_submission.id
        print('======================================')
        print('JobSubmission ID=' + str(job_submission_id))
        try:
            obj = json.loads(job_submission.obj)
            if 'user_email' not in obj['areaOfStudy']:
                print('field user_email not found, add it')
                reqGET = requests.get('http://frontend:8001/portal/users/' +
                                      str(job_submission.user_id), params={'hostname': 'intermediate:8002'})
                print(reqGET.status_code)
                if reqGET.status_code == 200:
                    cs = reqGET.json()
                    if cs['alert'] == 'success':
                        print('found user_email, add it to job submission')
                        obj['areaOfStudy']['user_email'] = cs['email']
                        job_submission.obj = json.dumps(obj)
                        job_submission.save()
                        print('done!')
                    else:
                        print('json endpoint throwed error, skip it')
                else:
                    print('endpoint failed, skip it')

            else:
                print('field user_email already added, nothing to do, skip')
        except Exception as e:
            print(str(e))
