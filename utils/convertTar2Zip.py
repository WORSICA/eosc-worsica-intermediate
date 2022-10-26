#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import requests
import subprocess
from worsica_api import utils

if __name__ == '__main__':
    import django

    sys.path.append("/usr/local/worsica_web_intermediate")
    os.environ['DJANGO_SETTINGS_MODULE'] = 'worsica_web_intermediate.settings'
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "worsica_web_intermediate.settings")

    django.setup()

    import worsica_api.models as worsica_api_models
    from worsica_web_intermediate import nextcloud_access

    job_submissions = worsica_api_models.JobSubmission.objects.filter(isVisible=True)
    for job_submission in job_submissions:
        job_submission_id = job_submission.id
        print('======================================')
        print('JobSubmission ID=' + str(job_submission_id))
        try:
            SERVICE = job_submission.service
            USER_ID = str(job_submission.user_id)
            ROI_ID = str(job_submission.aos_id)
            SIMULATION_ID = str(job_submission.simulation_id)
            WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + \
                str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
            NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER
            piss = worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission)
            for pis in piss:
                print('##################################')
                print('ProcessImageSet ID=' + str(pis.id))
                try:
                    IMAGESET_NAME = pis.name
                    kinds = ['resampled', 'final-products']
                    for kind in kinds:
                        try:
                            # download tar
                            fileName = SERVICE + '-user' + \
                                str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + \
                                str(SIMULATION_ID) + '-' + IMAGESET_NAME
                            print(fileName)
                            tarFileName = fileName + '-' + kind + '.tar.gz'
                            print('------------')
                            print('1) Download ' + tarFileName)
                            r = requests.get(
                                NEXTCLOUD_PATH_TO_PRODUCTS + '/' + tarFileName,
                                auth=(
                                    nextcloud_access.NEXTCLOUD_USER,
                                    nextcloud_access.NEXTCLOUD_PWD))
                            tmpPathTarFileName = '/tmp/' + tarFileName
                            if r.status_code == 200:
                                with open(tmpPathTarFileName, 'wb') as f:
                                    f.write(r.content)
                                # open tar
                                print('------------')
                                print('2) Open tar ' + tarFileName + ' and create zip')
                                zipFileName = fileName + '-' + kind + '.zip'
                                tmpPathZipFileName = '/tmp/' + zipFileName
                                COMMAND_SHELL1 = utils._build_command_shell_to_run(
                                    "tar -xzf " + tarFileName + " && zip " +
                                    zipFileName + " $(tar -tf " + tarFileName + ")",
                                    None)
                                print(COMMAND_SHELL1)
                                cmd1 = subprocess.Popen(
                                    COMMAND_SHELL1,
                                    cwd='/tmp/',
                                    shell=False,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
                                cmd_wait1 = cmd1.wait()
                                if cmd_wait1 == 0:
                                    print('------------')  # upload
                                    print('3) Success! Upload ' + zipFileName +
                                          ' to nextcloud at ' + NEXTCLOUD_PATH_TO_PRODUCTS)
                                    COMMAND_SHELL2 = utils._build_command_shell_to_run(
                                        "curl -u " + nextcloud_access.NEXTCLOUD_USER + ":" + nextcloud_access.NEXTCLOUD_PWD + " -T " + tmpPathZipFileName + " " +
                                        NEXTCLOUD_PATH_TO_PRODUCTS + "/" + zipFileName +
                                        " -w '<http_code>%{http_code}</http_code>' | grep -oP '(?<=<http_code>).*?(?=</http_code>)' ",
                                        None)
                                    print(COMMAND_SHELL2)
                                    cmd2 = subprocess.Popen(
                                        COMMAND_SHELL2, cwd='/tmp/', shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                                    out2, err2 = cmd2.communicate()
                                    if ("201" in str(out2) or "204" in str(out2)):
                                        print('------------')
                                        print('Upload successful, remove zip file from /tmp.')
                                        COMMAND_SHELL3 = utils._build_command_shell_to_run(
                                            "rm -rf " + zipFileName,
                                            None)
                                        print(COMMAND_SHELL3)
                                        cmd3 = subprocess.Popen(
                                            COMMAND_SHELL3, cwd='/tmp/', shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                                        cmd_wait3 = cmd3.wait()
                                        if cmd_wait3 == 0:
                                            r = requests.delete(
                                                NEXTCLOUD_PATH_TO_PRODUCTS + '/' + tarFileName,
                                                auth=(
                                                    nextcloud_access.NEXTCLOUD_USER,
                                                    nextcloud_access.NEXTCLOUD_PWD))
                                            if (r.status_code == 204):
                                                print('------------')
                                                print('Removed tar.gz file from nextcloud ' +
                                                      NEXTCLOUD_PATH_TO_PRODUCTS + '/' + tarFileName)
                                            else:
                                                print('Fail on remove tar.gz file from nextcloud')
                                            r = requests.delete(
                                                NEXTCLOUD_PATH_TO_PRODUCTS + '/' + tarFileName + '.sha1',
                                                auth=(
                                                    nextcloud_access.NEXTCLOUD_USER,
                                                    nextcloud_access.NEXTCLOUD_PWD))
                                            if (r.status_code == 204):
                                                print('------------')
                                                print('Removed tar.gz file from nextcloud ' +
                                                      NEXTCLOUD_PATH_TO_PRODUCTS + '/' + tarFileName)
                                            else:
                                                print('Fail on remove tar.gz.sha1 file from nextcloud')
                                        else:
                                            print('Fail on remove zip file from /tmp.')
                                    else:
                                        print('Fail on upload (' + str(out2) + ')')
                                else:
                                    print('Fail on opening tar')

                                print('------------')
                                print('4) cleanup tar.gz and other files in /tmp')
                                COMMAND_SHELL4 = utils._build_command_shell_to_run(
                                    "rm -rf " + SERVICE + " && rm -rf " + tarFileName,
                                    None)
                                print(COMMAND_SHELL4)
                                cmd4 = subprocess.Popen(
                                    COMMAND_SHELL4,
                                    cwd='/tmp/',
                                    shell=False,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
                                cmd_wait4 = cmd4.wait()
                                if cmd_wait4 == 0:
                                    print('Success!')
                                else:
                                    print('Fail on remove')
                            else:
                                print('File not found, skip')

                        except Exception as e:
                            print('Error at kind level: ' + str(e))
                            continue

                except Exception as e:
                    print('Error at ProcessImageSet level: ' + str(e))
                    continue
        except Exception as e:
            print('Error at JobSubmission level: ' + str(e))
            continue
