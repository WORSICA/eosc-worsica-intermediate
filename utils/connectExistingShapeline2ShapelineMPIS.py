#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
# add user_email to older submissions
if __name__ == '__main__':
    import django

    sys.path.append("/usr/local/worsica_web_intermediate")
    os.environ['DJANGO_SETTINGS_MODULE'] = 'worsica_web_intermediate.settings'
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "worsica_web_intermediate.settings")

    django.setup()

    import worsica_api.models as worsica_api_models

    job_submissions = worsica_api_models.JobSubmission.objects.filter(
        isVisible=True, service__in=['coastal', 'inland']).order_by('id')
    for job_submission in job_submissions:
        job_submission_id = job_submission.id
        print('======================================')
        print('JobSubmission ID=' + str(job_submission_id))
        try:
            # if any
            print('Check if has ProcessImageSet')
            piss = worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission)
            for pis in piss:
                print('ProcessImageSet ID=' + str(pis.id))
                shapelines = worsica_api_models.Shapeline.objects.filter(processImageSet=pis)
                shapelines_distinct = shapelines.distinct('name')
                if (len(shapelines) > 0):
                    print('Has shapelines, start moving out')
                    for shapeline in shapelines_distinct:
                        shapeline_group, is_created = worsica_api_models.ShapelineMPIS.objects.get_or_create(
                            processImageSet=pis, name=shapeline.name)
                        if is_created:
                            print('ShapelineMPIS was created with name ' + shapeline_group.name)
                        else:
                            print('ShapelineMPIS exists with name ' + shapeline_group.name)
                        print('Link shapeline ' + str(shapeline.name) +
                              ' with the new shapelineMPIS, and remove processImageset')
                        shapelines2 = shapelines.filter(name=shapeline.name)
                        shapelines2.update(shapelineMPIS=shapeline_group, processImageSet=None)
                        # print(shapelines2.count())
                        print('Done!')
                else:
                    print('No shapelines, skip')
            #
            mpiss = worsica_api_models.MergedProcessImageSet.objects.filter(
                jobSubmission=job_submission)
            print('Check if has MergedProcessImageSet')
            for mpis in mpiss:
                print('MergedProcessImageSet ID=' + str(mpis.id))
                shapelines = worsica_api_models.Shapeline.objects.filter(mprocessImageSet=mpis)
                shapelines_distinct = shapelines.distinct('name')
                if (len(shapelines) > 0):
                    print('Has shapelines, start moving out')
                    for shapeline in shapelines_distinct:
                        shapeline_group, is_created = worsica_api_models.ShapelineMPIS.objects.get_or_create(
                            mprocessImageSet=mpis, name=shapeline.name)
                        if is_created:
                            print('ShapelineMPIS was created with name ' + shapeline_group.name)
                        else:
                            print('ShapelineMPIS exists with name ' + shapeline_group.name)
                        print('Link shapeline ' + str(shapeline.name) +
                              ' with the new shapelineMPIS, and remove processImageset')
                        shapelines2 = shapelines.filter(name=shapeline.name)
                        # print(shapelines2.count())
                        shapelines2.update(shapelineMPIS=shapeline_group, mprocessImageSet=None)
                        print('Done!')

                else:
                    print('No shapelines, skip')
        except Exception as e:
            print(str(e))
