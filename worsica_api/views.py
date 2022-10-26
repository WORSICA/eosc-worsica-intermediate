from django.views.decorators.csrf import csrf_exempt
from django.utils.text import slugify
from django.http import HttpResponse
from django.core.files import File
from django.db import transaction

from django.contrib.gis.utils import LayerMapping
from django.contrib.gis.gdal import OGRGeometry, DataSource, GDALRaster
from django.contrib.gis.geos import GEOSGeometry
from osgeo import gdal
from django.core.exceptions import ObjectDoesNotExist

from django.db.models.signals import pre_save

import worsica_api.models as worsica_api_models
import worsica_api.utils as worsica_api_utils
import raster.models as raster_models
import json

import datetime
import time
from operator import itemgetter

import sys
import os
import shutil

import subprocess
import requests
import zipfile

import traceback

from worsica_web_intermediate import settings, nextcloud_access, dataverse_access
from . import logger, tasks, dataverse_import, subsubtasks, subtasks, utils
from celery.result import AsyncResult

import uptide
import numpy as np
from base64 import b64encode, b64decode

import matplotlib
import matplotlib.pyplot as plt
import geopandas

worsica_logger = logger.init_logger('WORSICA-Intermediate.Views', settings.LOG_PATH)
ACTUAL_PATH = os.getcwd()

# Allow parallelism
# PARALLELISM_ENABLED = True
PARALLELISM_NUM_WORKERS = 4


def _change_generate_topo_state(generate_topo, state):
    generate_topo.processState = state
    generate_topo.save()


def _change_job_submission_state(job_submission, state):
    job_submission.state = state
    job_submission.save()


def _change_leak_detection_state(ld, state):
    ld.processState = state
    ld.save()


def _change_imageset_state(IS_USER_CHOSEN, pi, state, ri=None):
    pi.processState = state
    pi.save()
    # if its being downloaded, synchronize processingimageset (pi) state with the repositoryimageset (ri) object
    if ri is not None and state in ['submitted', 'downloading', 'error-downloading', 'error-download-corrupt', 'downloaded',
                                    'converting', 'converted', 'error-converting', 'download-waiting-lta', 'download-timeout-retry', 'expired']:
        ri.state = state
        if state in ['submitted', 'error-downloading', 'error-download-corrupt', 'error-converting']:
            ri.managedByJobId = None
        elif state in ['downloading', 'download-waiting-lta', 'download-timeout-retry', 'converting']:
            if IS_USER_CHOSEN:
                print('Userchosen imageset')
                ri.managedByJobId = 'userchosen' + str(pi.user_chosen_product.id)
            else:
                print('Normal imageset')
                ri.managedByJobId = 'simulation' + str(pi.jobSubmission.id)
        elif state in ['downloaded', 'converted']:
            ri.managedByJobId = None
            worsica_logger.info('[_change_imageset_state_uc ' + state + ']: imageset ' + ri.name + ' will be reused, update downloadDate and expirationDate')
            _change_repository_imageset_dates(ri)
        ri.save()


def _change_repository_imageset_dates(ri):
    dtnow = datetime.datetime.now()
    ri.downloadDate = dtnow
    ri.expirationDate = dtnow + datetime.timedelta(days=30)
    ri.save()


def _find_word_in_output_file(s, file):
    with open(file, encoding="utf-8") as f:
        return s in f.read()


@csrf_exempt
def show_all_job_submissions(request):
    roi_id = request.GET.get('roi_id')
    job_submissionsJson = []
    job_submissions = worsica_api_models.JobSubmission.objects.filter(aos_id=roi_id)
    for job_submission in job_submissions:
        obj = json.loads(job_submission.obj)
        job_submissionsJson.append({
            "id": job_submission.id,
            "aos_id": job_submission.aos_id,
            "name": obj['areaOfStudy']['simulation']['name'],
            "state": job_submission.state,
            "service": job_submission.service
        })
    return HttpResponse(json.dumps(job_submissionsJson), content_type='application/json')


@csrf_exempt
def edit_job_submission(request, job_submission_id):
    try:
        jsonReq = json.loads(request.body.decode('utf-8'))
        exec_arguments = jsonReq['exec_arguments']
        obj = jsonReq['object']
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
        job_submission.service = obj['areaOfStudy']['service']
        job_submission.provider = exec_arguments['inputs']['provider']
        job_submission.state = 'submitted'
        job_submission.obj = json.dumps(obj)
        job_submission.exec_arguments = json.dumps(exec_arguments)
        job_submission.name = obj['areaOfStudy']['simulation']['name']  # job_submission.service+'-aos'+str(job_submission.aos_id)+'-job-submission'+str(job_submission.id)
        job_submission.reference = job_submission.service + '-aos' + str(job_submission.aos_id) + '-simulation' + str(job_submission.simulation_id) + '-job-submission' + str(job_submission.id)
        job_submission.save()
        # retrun created sim
        createdSim = {
            "id": job_submission.id,
            "aos_id": job_submission.aos_id,
            "name": obj['areaOfStudy']['simulation']['name'],
            "state": job_submission.state,
            "service": job_submission.service
        }
        return HttpResponse(json.dumps({"alert": "created", "job_submission": createdSim}), content_type='application/json')
    except Exception as e:
        return HttpResponse(json.dumps({"alert": "error", "exception": "worsica-web-intermediate:" + str(e)}), content_type='application/json')


@csrf_exempt
def update_job_submission(request, job_submission_id):
    _host = request.get_host()
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        job_submission.lastRunAt = datetime.datetime.now()
        job_submission.lastFinishedAt = None
        job_submission.save()
        worsica_logger.info('[update_job_submission]: run id=' + str(job_submission_id) + ' state=' + job_submission.state)
        task_identifier = get_task_identifier(job_submission)
        print(task_identifier)
        tasks.taskUpdateProcessing.apply_async(args=[job_submission.aos_id, job_submission.id, _host], task_id=task_identifier)  # delay(job_submission.aos_id, job_submission.id, _host)
        return HttpResponse(json.dumps({'state': 'updating', 'roi_id': job_submission.aos_id, 'id': job_submission.id}), content_type='application/json')
    except Exception as e:
        return HttpResponse(json.dumps({'state': 'error', 'roi_id': job_submission.aos_id, 'id': job_submission.id}), content_type='application/json')


def updateProcessing(roi_id, job_submission_id, req_host):
    # delete old processing
    job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
    SERVICE = job_submission.service
    USER_ID = job_submission.user_id
    ROI_ID = job_submission.aos_id
    SIMULATION_ID = job_submission.simulation_id
    WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
    if SERVICE == 'waterleak':
        job_submission.state = 'submitted'
        job_submission.save()
        # delete climatologies
        worsica_logger.info('[updateProcessing]: set all Climatologies to submitted')
        ampiss = worsica_api_models.AverageMergedProcessImageSet.objects.filter(jobSubmission=job_submission)
        ampiss.update(processState='submitted')
        # delete virtual imagesets
        worsica_logger.info('[updateProcessing]: set all merged imagesets to submitted')
        mpiss = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission)
        mpiss.update(processState='submitted')
        startProcessing(roi_id, job_submission_id, req_host)


@csrf_exempt
def restart_job_submission(request, job_submission_id):
    _host = request.get_host()
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        job_submission.lastRunAt = datetime.datetime.now()
        job_submission.lastFinishedAt = None
        job_submission.save()
        worsica_logger.info('[restart_job_submission]: run id=' + str(job_submission_id) + ' state=' + job_submission.state)
        task_identifier = get_task_identifier(job_submission)
        print(task_identifier)
        tasks.taskRestartProcessing.apply_async(args=[job_submission.aos_id, job_submission.id, _host], task_id=task_identifier)  # delay(job_submission.aos_id, job_submission.id, _host)
        return HttpResponse(json.dumps({'state': 'restarted', 'roi_id': job_submission.aos_id, 'id': job_submission.id}), content_type='application/json')
    except Exception as e:
        return HttpResponse(json.dumps({'state': 'error', 'roi_id': job_submission.aos_id, 'id': job_submission.id}), content_type='application/json')


def restartProcessing(roi_id, job_submission_id, req_host):
    # delete old processing
    job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
    SERVICE = job_submission.service
    USER_ID = job_submission.user_id
    ROI_ID = job_submission.aos_id
    SIMULATION_ID = job_submission.simulation_id
    WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
    PATH_TO_PRODUCTS = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/' + WORKSPACE_SIMULATION_FOLDER
    NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER
    if os.path.exists(PATH_TO_PRODUCTS):
        shutil.rmtree(PATH_TO_PRODUCTS)
        worsica_logger.info('[edit_job_submission]: remove ' + PATH_TO_PRODUCTS)

    r = requests.delete(NEXTCLOUD_PATH_TO_PRODUCTS, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
    if (r.status_code == 204):
        worsica_logger.info('[edit_job_submission]: remove nextcloud ' + NEXTCLOUD_PATH_TO_PRODUCTS)

    mpiss = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission)
    for mpis in mpiss:
        worsica_logger.info('[edit_job_submission]: remove shp mpis from ' + str(mpis.name))
        worsica_api_models.ShapelineMPIS.objects.filter(mprocessImageSet=mpis).delete()
    worsica_logger.info('[edit_job_submission]: remove all MergedProcessImageSet')
    mpiss.delete()

    mpiss = worsica_api_models.AverageMergedProcessImageSet.objects.filter(jobSubmission=job_submission)
    worsica_logger.info('[edit_job_submission]: remove all AverageMergedProcessImageSet')
    mpiss.delete()

    mpissld = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(jobSubmission=job_submission)
    worsica_logger.info('[edit_job_submission]: remove all MergedProcessImageSetForLeakDetection')
    mpissld.delete()

    piss = worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission)
    for pis in piss:
        worsica_logger.info('[edit_job_submission]: remove shp pis from ' + str(pis.name))
        worsica_api_models.ShapelineMPIS.objects.filter(processImageSet=pis).delete()
    worsica_logger.info('[edit_job_submission]: remove all ProcessImageSet')
    piss.delete()

    _topographies = worsica_api_models.GenerateTopographyMap.objects.filter(jobSubmission=job_submission)
    worsica_logger.info('[edit_job_submission]: remove all GenerateTopographyMap')
    _topographies.delete()

    _thresholds = worsica_api_models.ImageSetThreshold.objects.filter(processImageSet__jobSubmission=job_submission)
    worsica_logger.info('[edit_job_submission]: remove all ImageSetThreshold')
    _thresholds.delete()

    startProcessing(roi_id, job_submission_id, req_host)


@csrf_exempt
def create_job_submission(request):
    _host = request.get_host()
    try:
        jsonReq = json.loads(request.body.decode('utf-8'))
        exec_arguments = jsonReq['exec_arguments']
        obj = jsonReq['object']
        service = obj['areaOfStudy']['service']
        provider = exec_arguments['inputs']['provider']
        # for normal cases, user can create many submissions as he wishes for a roi
        job_submission = worsica_api_models.JobSubmission.objects.create(
            user_id=obj['areaOfStudy']['user_id'],
            aos_id=obj['areaOfStudy']['id'],
            simulation_id=obj['areaOfStudy']['simulation']['id'],
            service=service,
            provider=provider,
            state='submitted',
            obj=json.dumps(obj),
            exec_arguments=json.dumps(exec_arguments)
        )
        job_submission.name = obj['areaOfStudy']['simulation']['name']  # job_submission.service+'-aos'+str(job_submission.aos_id)+'-job-submission'+str(job_submission.id)
        job_submission.reference = job_submission.service + '-aos' + str(job_submission.aos_id) + '-simulation' + str(job_submission.simulation_id) + '-job-submission' + str(job_submission.id)
        job_submission.save()
        createdSim = {
            "id": job_submission.id,
            "aos_id": job_submission.aos_id,
            "name": obj['areaOfStudy']['simulation']['name'],
            "state": job_submission.state,
            "service": job_submission.service
        }
        return HttpResponse(json.dumps({"alert": "created", "job_submission": createdSim}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({"alert": "error", "exception": "worsica-web-intermediate-create_job_submission:" + str(e)}), content_type='application/json')


@csrf_exempt
def create_leak_detection(request, job_submission_id):
    _host = request.get_host()
    try:
        jsonReq = json.loads(request.body.decode('utf-8'))
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id)
        leak_detection_name = jsonReq['leakDetectionName']
        determine_leak_by = jsonReq['step1ImageSelection']['image_source']['detection_type']
        index_type = jsonReq['step1ImageSelection']['image_source']['index_type']
        leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.create(
            jobSubmission=job_submission,
            image_source_from=jsonReq['step1ImageSelection']['image_source']['from'],
            start_processing_second_deriv_from=determine_leak_by,
            indexType=index_type,
            exec_arguments=json.dumps(jsonReq)
        )
        leak_detection.small_name = leak_detection_name
        leak_detection.name = leak_detection_name  # job_submission.service+'-aos'+str(job_submission.aos_id)+'-job-submission'+str(job_submission.id)
        leak_detection.reference = job_submission.service + '-aos' + str(job_submission.aos_id) + '-simulation' + str(job_submission.simulation_id) + \
            '-job-submission' + str(job_submission.id) + '-ld' + str(leak_detection.id)
        leak_detection.save()
        createdLD = {
            "id": leak_detection.id,
            "job_submission_id": leak_detection.jobSubmission.id,
            "aos_id": leak_detection.jobSubmission.aos_id,
            "name": leak_detection_name,
            "state": leak_detection.processState,
            "service": leak_detection.jobSubmission.service
        }
        return HttpResponse(json.dumps({"alert": "created", "leak_detection": createdLD}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({"alert": "error", "exception": "worsica-web-intermediate-create_leak_detection:" + str(e)}), content_type='application/json')


@csrf_exempt
def edit_leak_detection(request, job_submission_id, leak_detection_id):
    try:
        jsonReq = json.loads(request.body.decode('utf-8'))
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id)
        leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(
            jobSubmission=job_submission, id=leak_detection_id,
        )
        pipeNetworkOption = jsonReq['step3LeakDetection']['optStep3LeakDetectionPipeNetwork']
        maskOption = jsonReq['step3LeakDetection']['optStep3LeakDetectionMask']
        leak_detection.filter_by_mask = ((pipeNetworkOption == 'optStep3LeakDetectionPipeNetwork1' or pipeNetworkOption ==
                                          'optStep3LeakDetectionPipeNetwork2') and maskOption == 'optStep3LeakDetectionMask1')
        leak_detection.exec_arguments = json.dumps(jsonReq)
        leak_detection.save()

        # return created sim
        createdLD = {
            "id": leak_detection.id,
            "job_submission_id": leak_detection.jobSubmission.id,
            "aos_id": leak_detection.jobSubmission.aos_id,
            "name": leak_detection.name,
            "state": leak_detection.processState,
            "service": leak_detection.jobSubmission.service
        }
        return HttpResponse(json.dumps({"alert": "created", "leak_detection": createdLD}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({"alert": "error", "exception": "worsica-web-intermediate-edit_leak_detection:" + str(e)}), content_type='application/json')


def get_leak_detection(request, job_submission_id, leak_detection_id):
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id)
        leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(
            jobSubmission=job_submission, id=leak_detection_id,
        )
        createdLD = {
            "id": leak_detection.id,
            "job_submission_id": leak_detection.jobSubmission.id,
            "job_submission_name": leak_detection.jobSubmission.name,
            "aos_id": leak_detection.jobSubmission.aos_id,
            "name": leak_detection.name,
            "state": leak_detection.processState,
            "service": leak_detection.jobSubmission.service,
            "image_source_from": leak_detection.image_source_from,
            "start_processing_second_deriv_from": leak_detection.start_processing_second_deriv_from,
            "indexType": leak_detection.indexType,
            "processImageSet": (leak_detection.processImageSet.name if leak_detection.processImageSet else None),
            "ucprocessImageSet": (leak_detection.ucprocessImageSet.name if leak_detection.ucprocessImageSet else None),
            "blob": json.loads(leak_detection.exec_arguments)
        }
        return HttpResponse(json.dumps({"alert": "success", "leak_detection": createdLD}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({"alert": "error", "exception": "worsica-web-intermediate-edit_leak_detection:" + str(e)}), content_type='application/json')


def get_task_identifier(job_submission):
    SERVICE = job_submission.service
    USER_ID = str(job_submission.user_id)
    ROI_ID = str(job_submission.aos_id)
    SIMULATION_ID = str(job_submission.simulation_id)
    task_identifier = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-simulation' + str(SIMULATION_ID) + '-' + str(job_submission.lastRunAt.strftime("%Y%m%d_%H%M%S"))
    return task_identifier


def get_task_identifier_uc(user_chosen_product):
    SERVICE = user_chosen_product.service
    USER_ID = str(user_chosen_product.user_id)
    ROI_ID = str(user_chosen_product.aos_id)
    USER_CHOSEN_PRODUCT_ID = str(user_chosen_product.id)
    task_identifier = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-uc' + str(USER_CHOSEN_PRODUCT_ID) + '-' + str(user_chosen_product.lastRunAt.strftime("%Y%m%d_%H%M%S"))
    return task_identifier


def get_task_identifier_generate_topo(job_submission, gt_id):
    SERVICE = job_submission.service
    USER_ID = str(job_submission.user_id)
    ROI_ID = str(job_submission.aos_id)
    SIMULATION_ID = str(job_submission.simulation_id)
    task_identifier = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-simulation' + str(SIMULATION_ID) + '-gt' + str(gt_id) + '-' + str(job_submission.lastRunAt.strftime("%Y%m%d_%H%M%S"))
    return task_identifier


def get_task_identifier_cloned_shapeline_group(job_submission, csg_id):
    SERVICE = job_submission.service
    USER_ID = str(job_submission.user_id)
    ROI_ID = str(job_submission.aos_id)
    SIMULATION_ID = str(job_submission.simulation_id)
    task_identifier = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-simulation' + str(SIMULATION_ID) + '-csg' + str(csg_id) + '-' + str(job_submission.lastRunAt.strftime("%Y%m%d_%H%M%S"))
    return task_identifier


def get_task_identifier_created_threshold(job_submission, ct_id):
    SERVICE = job_submission.service
    USER_ID = str(job_submission.user_id)
    ROI_ID = str(job_submission.aos_id)
    SIMULATION_ID = str(job_submission.simulation_id)
    task_identifier = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-simulation' + str(SIMULATION_ID) + '-ct' + str(ct_id) + '-' + str(job_submission.lastRunAt.strftime("%Y%m%d_%H%M%S"))
    return task_identifier


def get_grid_job_id(job_submission):
    SERVICE = job_submission.service
    USER_ID = str(job_submission.user_id)
    ROI_ID = str(job_submission.aos_id)
    SIMULATION_ID = str(job_submission.simulation_id)
    task_identifier = str(job_submission.lastRunAt.strftime("%Y%m%d_%H%M%S") + '-worsica-processing-service-' +
                          SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-simulation' + str(SIMULATION_ID))
    return task_identifier


def stop_grid_submission(get_grid_job_id_js, pis_id, js_state):
    if settings.ENABLE_GRID:
        # 20220517-172643-worsica-processing-service-coastal-user1-roi144-simulation170-1719-converting
        job_id_file = get_grid_job_id_js + '-' + str(pis_id) + '-' + js_state
        print(job_id_file)
        # worsica-processing-service-coastal-user1-roi144-simulation170-1719-converting
        job_id_file = job_id_file[16:]  # remove timestamp
        print('Kill grid job ' + job_id_file)
        COMMAND_SHELL = utils._build_command_shell_to_run(
            './worsica_grid_kill_submission.sh ' + job_id_file,
            './log/' + job_id_file + '.kill.debug',
            False)
        try:
            cmd = subprocess.Popen(COMMAND_SHELL, cwd=settings.WORSICA_FOLDER_PATH + '/worsica_web_products/', shell=False, preexec_fn=os.setsid)
            # cmd_wait = cmd.wait(timeout=60) #60 seconds
            print('Killed grid job ' + job_id_file)
        except Exception as e:
            print('Failed killing grid job ' + job_id_file)
            print(traceback.format_exc())


@csrf_exempt
def stop_job_submission(request, job_submission_id):
    _host = request.get_host()
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        tasks.taskStopProcessing.apply_async(args=[job_submission.aos_id, job_submission.id, _host])  # delay(job_submission.aos_id, job_submission.id, _host)
        return HttpResponse(json.dumps({'state': 'stopped', 'roi_id': job_submission.aos_id, 'id': job_submission.id}), content_type='application/json')
    except Exception as e:
        return HttpResponse(json.dumps({'state': 'error', 'roi_id': job_submission.aos_id, 'id': job_submission.id}), content_type='application/json')


def stopProcessing(roi_id, job_submission_id, req_host):
    try:
        print('stopProcessing celery: app.control.revoke')
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = roi_id)
        task_identifier = get_task_identifier(job_submission)
        print(task_identifier)
        if job_submission.state in ['submitted', 'success']:
            print('Do nothing, because this job is not running (' + job_submission.state + ')')
        elif 'error' in job_submission.state:
            print('Do nothing, because this job stopped with an error (' + job_submission.state + ')')
        else:
            PREV_JOB_SUBMISSION_STATE = job_submission.state
            print('AsyncResult')
            asyncResult = AsyncResult(task_identifier)
            print(asyncResult)
            asyncResult.revoke(terminate=True, signal='SIGKILL')
            print("stopProcessing: process stopped")
            # if PARALLELISM_ENABLED:
            for step in ['downloading', 'converting', 'resampling', 'merging', 'storing-rgb', 'generating-virtual-img',
                         'interpolating-products', 'processing', 'storing-processing', 'generating-average-img', 'storing-generating-average']:
                for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                    print("stopProcessing: parallelism is enabled, stop any working children")
                    asyncResultChild = AsyncResult(task_identifier + '-' + step + '-child' + str(pw))
                    print(asyncResultChild)
                    asyncResultChild.revoke(terminate=True, signal='SIGKILL')
                    print("stopProcessing: " + step + " childrens stopped")
            # downloading
            if job_submission.state in ['downloading', 'download-waiting-lta', 'download-timeout-retry']:
                _change_job_submission_state(job_submission, 'error-downloading')
                for pis in worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['downloading', 'download-waiting-lta', 'download-timeout-retry']):
                    # stop_grid_submission
                    stop_grid_submission(get_grid_job_id(job_submission), str(pis.id), PREV_JOB_SUBMISSION_STATE)
                    # check if repositoryimageset is associated to that job, and unlock it after changing state to error-downloading, otherwise, do nothing
                    if pis.imageSet is not None and pis.imageSet.managedByJobId is not None and (pis.imageSet.managedByJobId == 'simulation' + str(job_submission.id)):
                        _change_imageset_state(False, pis, 'error-downloading', pis.imageSet)
                    else:
                        _change_imageset_state(False, pis, 'error-downloading')
                for mpis in worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission):
                    _change_imageset_state(False, mpis, 'submitted')
            # converting
            elif job_submission.state in ['converting', 'converted']:
                _change_job_submission_state(job_submission, 'error-converting')
                for pis in worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['converting']):
                    # stop_grid_submission
                    stop_grid_submission(get_grid_job_id(job_submission), str(pis.id), PREV_JOB_SUBMISSION_STATE)
                    # check if repositoryimageset is associated to that job, and unlock it after changing state to error-downloading, otherwise, do nothing
                    if pis.imageSet.childRepositoryImageSet is not None and pis.imageSet.childRepositoryImageSet.managedByJobId is not None and (
                            pis.imageSet.childRepositoryImageSet.managedByJobId == 'simulation' + str(job_submission.id)):
                        _change_imageset_state(False, pis, 'error-converting', pis.imageSet.childRepositoryImageSet)
                    else:
                        _change_imageset_state(False, pis, 'error-converting')
                for mpis in worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission):
                    _change_imageset_state(False, mpis, 'submitted')
            # resampling
            elif job_submission.state in ['resampling']:
                _change_job_submission_state(job_submission, 'error-resampling')
                for pis in worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['resampling']):
                    # stop_grid_submission
                    stop_grid_submission(get_grid_job_id(job_submission), str(pis.id), PREV_JOB_SUBMISSION_STATE)
                    #
                    _change_imageset_state(False, pis, 'error-resampling')
                for mpis in worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission):
                    _change_imageset_state(False, mpis, 'submitted')
                for mpis in worsica_api_models.AverageMergedProcessImageSet.objects.filter(jobSubmission=job_submission):
                    _change_imageset_state(False, mpis, 'submitted')
            # merging
            elif job_submission.state in ['merging']:
                _change_job_submission_state(job_submission, 'error-merging')
                for mpis in worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['merging']):
                    # stop_grid_submission
                    stop_grid_submission(get_grid_job_id(job_submission), str(mpis.id), PREV_JOB_SUBMISSION_STATE)
                    #
                    _change_imageset_state(False, mpis, 'error-merging')
                # store-rgb
                for mpis in worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['storing-rgb']):
                    _change_imageset_state(False, mpis, 'error-storing-rgb')
                for mpis in worsica_api_models.AverageMergedProcessImageSet.objects.filter(jobSubmission=job_submission):
                    _change_imageset_state(False, mpis, 'submitted')
            # generating-virtual
            elif job_submission.state in ['generating-virtual']:
                _change_job_submission_state(job_submission, 'error-generating-virtual')
                for mpis in worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['generating-virtual']):
                    # stop_grid_submission
                    stop_grid_submission(get_grid_job_id(job_submission), str(mpis.id), PREV_JOB_SUBMISSION_STATE)
                    #
                    _change_imageset_state(False, mpis, 'error-generating-virtual')
                for mpis in worsica_api_models.AverageMergedProcessImageSet.objects.filter(jobSubmission=job_submission):
                    _change_imageset_state(False, mpis, 'submitted')
            # interpolation
            elif job_submission.state in ['interpolating-products']:
                _change_job_submission_state(job_submission, 'error-interpolating-products')
                for mpis in worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['interpolating-products']):
                    # stop_grid_submission
                    stop_grid_submission(get_grid_job_id(job_submission), str(mpis.id), PREV_JOB_SUBMISSION_STATE)
                    #
                    _change_imageset_state(False, mpis, 'error-interpolating-products')
                for mpis in worsica_api_models.AverageMergedProcessImageSet.objects.filter(jobSubmission=job_submission):
                    _change_imageset_state(False, mpis, 'submitted')
            # processing
            elif job_submission.state in ['processing']:
                _change_job_submission_state(job_submission, 'error-processing')
                for pis in worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['processing']):
                    # stop_grid_submission
                    stop_grid_submission(get_grid_job_id(job_submission), str(mpis.id), PREV_JOB_SUBMISSION_STATE)
                    #
                    _change_imageset_state(False, pis, 'error-processing')
                # store-processing
                for pis in worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['storing-processing']):
                    _change_imageset_state(False, pis, 'error-storing-processing')
                for mpis in worsica_api_models.AverageMergedProcessImageSet.objects.filter(jobSubmission=job_submission):
                    _change_imageset_state(False, mpis, 'submitted')
            # generating-average
            elif job_submission.state in ['generating-average']:
                _change_job_submission_state(job_submission, 'error-generating-average')
                for mpis in worsica_api_models.AverageMergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['generating-average']):
                    # stop_grid_submission
                    stop_grid_submission(get_grid_job_id(job_submission), str(mpis.id), PREV_JOB_SUBMISSION_STATE)
                    #
                    _change_imageset_state(False, mpis, 'error-generating-average')
                # store-generating-avg
                for mpis in worsica_api_models.AverageMergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['storing-generating-average']):
                    _change_imageset_state(False, mpis, 'error-storing-generating-average')

            # send email
            worsica_api_utils.notify_processing_state(job_submission)

    except Exception as e:
        print(traceback.format_exc())


@csrf_exempt
def stop_job_submission_leak_detection(request, job_submission_id, leak_detection_id):
    _host = request.get_host()
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = roi_id)
        # throw error for missing masks/pipe networks if its running cleanup leaks
        # processImageSet=merged_imageset, start_processing_second_deriv_from = determine_leak_by)
        merged_imageset_ld = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(jobSubmission=job_submission, id=leak_detection_id)
    except Exception as e:
        print(traceback.format_exc())
        pass
    try:
        worsica_logger.info('[stop_job_submission_leak_detection]: run id=' + str(job_submission_id) + ' leak_detection_id=' + str(leak_detection_id) + ' state=' + job_submission.state)
        tasks.taskStopProcessingLeakDetection.apply_async(args=[job_submission.aos_id, job_submission.id, leak_detection_id, _host])
        return HttpResponse(json.dumps({'state': 'stopped', 'roi_id': job_submission.aos_id, 'id': job_submission.id, 'leak_detection_id': leak_detection_id}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'state': 'error', 'roi_id': job_submission.aos_id, 'id': job_submission.id,
                                        'leak_detection_id': leak_detection_id, 'error': str(e)}), content_type='application/json')


def stopProcessingLeakDetection(job_submission_id, leak_detection_id, req_host):
    try:
        print('stopProcessing celery: app.control.revoke')
        leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(id=leak_detection_id, jobSubmission__id=job_submission_id)
        if leak_detection.processState in ['submitted', 'success']:
            print('Do nothing, because this job is not running (' + leak_detection.processState + ')')
        elif 'error' in leak_detection.processState:
            print('Do nothing, because this job stopped with an error (' + leak_detection.processState + ')')
        else:
            LD_IMAGE_SOURCE_FROM = leak_detection.image_source_from
            user_chosen_products = worsica_api_models.UserChosenProduct.objects.filter(managedByLeakDetectionId='ld' + str(leak_detection_id))
            for user_chosen_product in user_chosen_products:
                task_identifier = get_task_identifier_uc(user_chosen_product)
                print(task_identifier)
                for step in ['downloading', 'converting', 'resampling', 'merging', 'storing-rgb', 'generating-virtual-img',
                             'interpolating-products', 'processing', 'storing-processing', 'generating-average-img', 'storing-generating-average']:
                    for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                        print("stopProcessingLeakDetection: parallelism is enabled, stop any working children")
                        asyncResultChild = AsyncResult(task_identifier + '-' + step + '-child' + str(pw))
                        print(asyncResultChild)
                        asyncResultChild.revoke(terminate=True, signal='SIGKILL')
                        print("stopProcessingLeakDetection: " + step + " childrens stopped")

            if LD_IMAGE_SOURCE_FROM == 'new_image':
                user_chosen_mpis = leak_detection.ucprocessImageSet  # UserChosenMergedProcessImageSet
                print(user_chosen_mpis)
                if (user_chosen_mpis is not None):
                    user_chosen_mpis_procImageSet_ids = user_chosen_mpis.procImageSet_ids.split(',')
                    user_chosen_mpis_id = user_chosen_mpis.id
                    user_chosen_products = worsica_api_models.UserChosenProduct.objects.filter(managedByLeakDetectionId='ld' + str(leak_detection_id))
                    for user_chosen_product in user_chosen_products:
                        # downloading
                        if user_chosen_product.state in ['downloading', 'download-waiting-lta', 'download-timeout-retry']:
                            _change_leak_detection_state(leak_detection, 'error-downloading')
                            _change_job_submission_state(user_chosen_product, 'error-downloading')
                            for pis in worsica_api_models.UserChosenProcessImageSet.objects.filter(id__in=user_chosen_mpis_procImageSet_ids, processState__in=[
                                    'downloading', 'download-waiting-lta', 'download-timeout-retry']):
                                # check if repositoryimageset is associated to that job, and unlock it after changing state to error-downloading, otherwise, do nothing
                                if pis.imageSet is not None and pis.imageSet.managedByJobId is not None and (pis.imageSet.managedByJobId == 'simulation' + str(job_submission_id)):
                                    _change_imageset_state(True, pis, 'error-downloading', pis.imageSet)
                                else:
                                    _change_imageset_state(True, pis, 'error-downloading')
                            for mpis in worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(id=user_chosen_mpis_id):
                                _change_imageset_state(True, mpis, 'submitted')
                        # resampling
                        elif user_chosen_product.state in ['resampling']:
                            _change_leak_detection_state(leak_detection, 'error-resampling')
                            _change_job_submission_state(user_chosen_product, 'error-resampling')
                            for pis in worsica_api_models.UserChosenProcessImageSet.objects.filter(id__in=user_chosen_mpis_procImageSet_ids, processState__in=['resampling']):
                                _change_imageset_state(True, pis, 'error-resampling')
                            for mpis in worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(id=user_chosen_mpis_id):
                                _change_imageset_state(True, mpis, 'submitted')
                        # merging
                        elif user_chosen_product.state in ['merging']:
                            _change_leak_detection_state(leak_detection, 'error-merging')
                            _change_job_submission_state(user_chosen_product, 'error-merging')
                            for mpis in worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(id=user_chosen_mpis_id, processState__in=['merging']):
                                _change_imageset_state(True, mpis, 'error-merging')
                        # processing
                        elif user_chosen_product.state in ['processing']:
                            _change_leak_detection_state(leak_detection, 'error-processing')
                            _change_job_submission_state(user_chosen_product, 'error-processing')
                            for pis in worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(id=user_chosen_mpis_id, processState__in=['processing']):
                                _change_imageset_state(True, pis, 'error-processing')
                            # store-processing
                            for pis in worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(id=user_chosen_mpis_id, processState__in=['storing-processing']):
                                _change_imageset_state(True, pis, 'error-storing-processing')

                        user_chosen_product.managedByLeakDetectionId = None
                        user_chosen_product.save()

            LD_SECOND_DERIV_FROM = leak_detection.start_processing_second_deriv_from  # determine_leak_by
            if (LD_SECOND_DERIV_FROM == 'by_anomaly'):
                if (leak_detection.processState in ['calculating-diff']):  # if calculating-diff
                    _change_leak_detection_state(leak_detection, 'error-calculating-diff')
                if (leak_detection.processState in ['storing-calculated-diff']):  # if storing-calculated-diff
                    _change_leak_detection_state(leak_detection, 'error-storing-calculated-diff')

            if (leak_detection.processState in ['calculating-leak']):  # if calculating-leak
                _change_leak_detection_state(leak_detection, 'error-calculating-leak')
            if (leak_detection.processState in ['storing-calculated-leak']):  # if storing-calculated-leak
                _change_leak_detection_state(leak_detection, 'error-storing-calculated-leak')

            FILTER_BY_MASK = leak_detection.filter_by_mask
            if (FILTER_BY_MASK and leak_detection.processState in ['generating-mask-leak']):
                _change_leak_detection_state(leak_detection, 'error-generating-mask-leak')

            if (leak_detection.processState in ['determinating-leak']):
                _change_leak_detection_state(leak_detection, 'error-determinating-leak')
            if (leak_detection.processState in ['storing-determinated-leak']):
                _change_leak_detection_state(leak_detection, 'error-storing-determinated-leak')

            # send email
            leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(id=leak_detection_id, jobSubmission__id=job_submission_id)
            worsica_api_utils.notify_leakdetection_processing_state(leak_detection)

    except Exception as e:
        print(traceback.format_exc())


@csrf_exempt
def run_job_submission_leak_detection(request, job_submission_id, leak_detection_id):
    _host = request.get_host()
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = roi_id)
        # throw error for missing masks/pipe networks if its running cleanup leaks
        # processImageSet=merged_imageset, start_processing_second_deriv_from = determine_leak_by)
        merged_imageset_ld = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(id=leak_detection_id)
    except Exception as e:
        print(traceback.format_exc())
        pass
    try:
        worsica_logger.info('[run_job_submission_leak_detection]: run id=' + str(job_submission_id) + ' leak_detection_id=' + str(leak_detection_id) + ' state=' + job_submission.state)
        task_identifier = get_task_identifier(job_submission) + '-ld' + str(leak_detection_id)  # +'-'+determine_leak_by
        print(task_identifier)
        tasks.taskStartProcessingLeakDetection.apply_async(args=[job_submission.aos_id, job_submission.id, leak_detection_id, _host], task_id=task_identifier)
        return HttpResponse(json.dumps({'state': 'created', 'roi_id': job_submission.aos_id, 'id': job_submission.id, 'leak_detection_id': leak_detection_id}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'state': 'error', 'roi_id': job_submission.aos_id, 'id': job_submission.id,
                                        'leak_detection_id': leak_detection_id, 'error': str(e)}), content_type='application/json')


# Part 1: (anomaly)+2nd deriv
def startProcessingLeakDetection(roi_id, job_submission_id, leak_detection_id, req_host):
    job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, aos_id=roi_id, isVisible=True)
    job_submission_exec = json.loads(job_submission.exec_arguments)

    PROVIDER = job_submission.provider
    SERVICE = job_submission.service
    USER_ID = str(job_submission.user_id)
    ROI_ID = str(job_submission.aos_id)
    SIMULATION_ID = str(job_submission.simulation_id)
    JOB_SUBMISSION_NAME = job_submission.name.encode('ascii', 'ignore').decode('ascii')
    GEOJSON_POLYGON = '\"' + job_submission_exec['roi'] + '\"'
    BATH_VALUE = str(job_submission_exec['detection']['bathDepth'])
    TOPO_VALUE = str(job_submission_exec['detection']['topoHeight'])
    WI_THRESHOLD = str(job_submission_exec['detection']['wiThreshold'])
    WATER_INDEX = str(job_submission_exec['detection']['waterIndex'])  # keep this one for the case of new image

    leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(id=leak_detection_id, jobSubmission__id=job_submission_id)
    leak_detection_exec = json.loads(leak_detection.exec_arguments)
    LD_WATER_INDEX = leak_detection.indexType  # this one is to assume the water index we really want to process!
    LD_IMAGE_SOURCE_FROM = leak_detection.image_source_from
    LD_SECOND_DERIV_FROM = leak_detection.start_processing_second_deriv_from  # determine_leak_by

    WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
    PATH_TO_PRODUCTS = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/' + WORKSPACE_SIMULATION_FOLDER
    NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER

    worsica_logger.info('[startProcessingLeakDetection]: ' + JOB_SUBMISSION_NAME + ' ' + SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) +
                        '-simulation' + str(SIMULATION_ID) + '-ld' + str(leak_detection_id) + '-' + LD_SECOND_DERIV_FROM + ' -- ' + job_submission.state)
    timestamp = datetime.datetime.today().strftime('%Y%m%d-%H%M%S')  # job_submission.lastRunAt.strftime('%Y%m%d-%H%M%S') #

    LOGS_FOLDER = './log'
    LOG_FILENAME = str(timestamp) + '-worsica-processing-service-' + SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + \
        '-simulation' + str(SIMULATION_ID) + '-ld' + str(leak_detection_id)  # +'-'+LD_SECOND_DERIV_FROM

    mpiss = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(id=leak_detection_id,
                                                                                    jobSubmission__id=job_submission_id)  # start_processing_second_deriv_from = LD_SECOND_DERIV_FROM)
    task_identifier = get_task_identifier(job_submission) + '-ld' + str(leak_detection_id)  # +'-'+LD_SECOND_DERIV_FROM
    print(task_identifier)

    print('====START====')
    print('LD_SECOND_DERIV_FROM = ' + LD_SECOND_DERIV_FROM)
    print('state = ' + leak_detection.processState)
    if LD_IMAGE_SOURCE_FROM == 'processed_image':
        merged_imageset_id = int(leak_detection_exec['step1ImageSelection']['image_source']['image_source_id'])
        merged_imageset = worsica_api_models.MergedProcessImageSet.objects.get(id=merged_imageset_id)
        leak_detection.processImageSet = merged_imageset
        leak_detection.save()
    elif LD_IMAGE_SOURCE_FROM == 'new_image':
        # 0- preparatory
        imagesets = leak_detection_exec['step1ImageSelection']['image_source']['listOfImagesets']
        for imageset in imagesets:
            # TODO: GIVE A DECENT NAME TO THIS OBJECT
            # i want to get the name and date of the imagesets
            name_split = imageset['name'].split('_')  # "S2A_MSIL2A_20210315T112111_N0214_R037_T29SNC_20210315T141433"
            timestamp_split = name_split[2].split('T')
            date = timestamp_split[0]  # +' '+timestamp_split[1]
            # date = name_split[2].split('T')[0]#20210315
            ucp_name = "ESA images of " + str(date)
            user_chosen_product, _ = worsica_api_models.UserChosenProduct.objects.get_or_create(
                user_id=USER_ID, aos_id=ROI_ID, service=SERVICE, provider=PROVIDER,
                name=ucp_name, imageDate=datetime.datetime.strptime(date, '%Y%m%d')
            )

        USER_CHOSEN_PRODUCT_ID = str(user_chosen_product.id)
        user_chosen_product.lastRunAt = datetime.datetime.now()
        user_chosen_product.lastFinishedAt = None
        user_chosen_product.reference = slugify(user_chosen_product.name)
        user_chosen_product.save()

        #
        if (leak_detection.ucprocessImageSet is None):
            try:
                print('[startProcessingLeakDetection MergedProcessImageSetForLeakDetection]: Link UserChosenMergedProcessImageSet with LeakDetection...')
                leak_detection.ucprocessImageSet = worsica_api_models.UserChosenMergedProcessImageSet.objects.get(user_chosen_product=user_chosen_product)
                leak_detection.save()
            except Exception as e:
                print(e)
                pass

        user_chosen_product = worsica_api_models.UserChosenProduct.objects.get(id=USER_CHOSEN_PRODUCT_ID)
        if (user_chosen_product.managedByLeakDetectionId is None):  # if is not being run by a leak detection
            worsica_logger.info('[startProcessingLeakDetection UserChosenProduct]: UserChosenProduct was found')
            user_chosen_product.managedByLeakDetectionId = 'ld' + str(leak_detection_id)
            user_chosen_product.save()
            if (user_chosen_product.state in ['submitted', 'error-downloading', 'error-download-corrupt']):
                try:
                    PREV_JOB_SUBMISSION_STATE = user_chosen_product.state
                    _change_job_submission_state(user_chosen_product, 'downloading')
                    _change_leak_detection_state(leak_detection, 'downloading')
                    # create repositoryimagesets, UserChosenProcessImageSet and UserChosenMergedProcessImageSet
                    for imageset in imagesets:
                        UUID = imageset['uuid']
                        IMAGESET_NAME = imageset['name']  # imageset['reference']
                        SIMULATION_NAME = imageset['name']
                        # we need to handle the concurrency during downloads.
                        # If 2 users download both the same file and download script is not ready to handle such concurrency, we need to handle that as a dijkstra semaphore,
                        # in order to not compromise the processing.
                        # check an entry for downloaded repository imageset
                        dis, dis_created = worsica_api_models.RepositoryImageSets.objects.get_or_create(name=IMAGESET_NAME, uuid=str(UUID))
                        if dis_created:  # if doesnt exist, create
                            worsica_logger.info('[startProcessingLeakDetection RepositoryImageSets]: add imageset ' + IMAGESET_NAME + ' to the RepositoryImageSets ')
                            dis.filePath = nextcloud_access.NEXTCLOUD_URL_PATH + '/repository_images/' + IMAGESET_NAME + '.zip'
                            dis.save()
                        else:
                            worsica_logger.info('[startProcessingLeakDetection RepositoryImageSets]: imageset ' + IMAGESET_NAME + ' already exists on the RepositoryImageSets')
                            if dis.state in ['expired']:
                                worsica_logger.info('[startProcessingLeakDetection RepositoryImageSets]: imageset ' + IMAGESET_NAME + ' expired, set submitted and update dates')
                                _change_repository_imageset_dates(dis)
                                dis.managedByJobId = None
                                dis.state = 'submitted'
                                dis.save()

                        # create UserChosenProcessImageSet
                        pis, pis_created = worsica_api_models.UserChosenProcessImageSet.objects.get_or_create(user_chosen_product=user_chosen_product, imageSet=dis)
                        pis.name = dis.name
                        pis.small_name = imageset['small_name']
                        pis.reference = dis.reference
                        pis.uuid = dis.uuid
                        pis.filePath = dis.filePath
                        # for water leak, do NOT start again from downloaded state
                        # must remain its actual state
                        # TODO:
                        if SERVICE != 'waterleak':
                            pis.processState = ('submitted' if dis.state == 'expired' else dis.state)
                        pis.convertToL2A = imageset['convertToL2A']
                        pis.save()
                        if pis_created:  # if doesnt exist, create
                            worsica_logger.info('[startProcessingLeakDetection UserChosenProcessImageSet]: add imageset ' + IMAGESET_NAME + ' to the UserChosenProcessImageSet of the UserChosenPoduct')
                        else:
                            worsica_logger.info('[startProcessingLeakDetection UserChosenProcessImageSet]: imageset ' + IMAGESET_NAME +
                                                ' already exists on the UserChosenProcessImageSet of the UserChosenPoduct')
                    # create UserChosenMergedProcessImageSet
                    piss = worsica_api_models.UserChosenProcessImageSet.objects.filter(user_chosen_product=user_chosen_product)
                    for pis in piss:  # wipe procimageset ids
                        timestamp = pis.name.split('_')[2]
                        timestamp_split = timestamp.split('T')
                        date = timestamp_split[0] + '_' + timestamp_split[1]
                        name = 'uc_merged_resampled_' + str(date)
                        mpis, mpis_created = worsica_api_models.UserChosenMergedProcessImageSet.objects.get_or_create(user_chosen_product=user_chosen_product, name=name)
                        sd = datetime.datetime.strptime(timestamp, '%Y%m%dT%H%M%S')
                        mpis.small_name = 'User chosen merged images of ' + str(sd)
                        mpis.sensingDate = sd
                        mpis.procImageSet_ids = ''
                        mpis.save()
                    for pis in piss:  # rebuild procimageset ids
                        timestamp = pis.name.split('_')[2]
                        timestamp_split = timestamp.split('T')
                        date = timestamp_split[0] + '_' + timestamp_split[1]
                        name = 'uc_merged_resampled_' + str(date)
                        mpis = worsica_api_models.UserChosenMergedProcessImageSet.objects.get(user_chosen_product=user_chosen_product, name=name)
                        mpis.procImageSet_ids += (',' if mpis.procImageSet_ids != '' else '') + str(pis.id)
                        mpis.save()

                    if (leak_detection.ucprocessImageSet is None):
                        try:
                            print('[startProcessingLeakDetection MergedProcessImageSetForLeakDetection]: Link UserChosenMergedProcessImageSet with LeakDetection...')
                            leak_detection.ucprocessImageSet = worsica_api_models.UserChosenMergedProcessImageSet.objects.get(user_chosen_product=user_chosen_product)
                            leak_detection.save()
                        except Exception as e:
                            print(e)
                            pass

                    # 1- download
                    piss = worsica_api_models.UserChosenProcessImageSet.objects.filter(
                        user_chosen_product=user_chosen_product,
                        processState__in=[
                            'submitted',
                            'error-downloading',
                            'error-download-corrupt',
                            'download-waiting-lta',
                            'download-timeout-retry']).order_by('-small_name')
                    result = []
                    try:
                        print('[startProcessingLeakDetection UserChosenProcessImageSet]: Start downloading...')
                        task_identifier = get_task_identifier_uc(user_chosen_product)
                        print(task_identifier)
                        for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                            print('[startProcessingLeakDetection UserChosenProcessImageSet]: Launch children ' + str(pw))
                            _tid = task_identifier + '-downloading-child' + str(pw)
                            result.append(
                                tasks.taskStartProcessingDownload.starmap(
                                    [
                                        (pis.id,
                                         SERVICE,
                                         USER_ID,
                                         ROI_ID,
                                         USER_CHOSEN_PRODUCT_ID,
                                         PREV_JOB_SUBMISSION_STATE,
                                         LOGS_FOLDER,
                                         LOG_FILENAME,
                                         (pis.id %
                                          PARALLELISM_NUM_WORKERS) +
                                         1,
                                         True,
                                         req_host) for pis in piss if (
                                            pis.id %
                                            PARALLELISM_NUM_WORKERS) +
                                        1 == pw]).apply_async(
                                    task_id=_tid))
                            time.sleep(2)  # wait 2 seconds for each async launches, to avoid race conditions at the beginning
                        piss_downloading = worsica_api_models.UserChosenProcessImageSet.objects.filter(
                            user_chosen_product=user_chosen_product, processState__in=[
                                'submitted', 'downloading', 'download-waiting-lta', 'download-timeout-retry']).order_by('-small_name')
                        while piss_downloading.count() > 0:
                            print('[startProcessingLeakDetection UserChosenProcessImageSet]: There are still images to be downloaded, waiting children to finish...')
                            time.sleep(10)
                            piss_downloading = worsica_api_models.UserChosenProcessImageSet.objects.filter(
                                user_chosen_product=user_chosen_product, processState__in=[
                                    'submitted', 'downloading', 'download-waiting-lta', 'download-timeout-retry']).order_by('-small_name')
                        print('[startProcessingLeakDetection UserChosenProcessImageSet]: Stop waiting, moving on...')
                    except Exception as e:
                        print(traceback.format_exc())
                        if len(result) > 0:
                            print('[startProcessingLeakDetection UserChosenProcessImageSet]: Revoking children to finish...')
                            for r in result:
                                r.revoke(terminate=True, signal='SIGKILL')
                    print('[startProcessingLeakDetection UserChosenProcessImageSet]: Make sure there is no download retry due to error.')
                    piss_downloading = worsica_api_models.UserChosenProcessImageSet.objects.filter(
                        user_chosen_product=user_chosen_product, processState__in=[
                            'submitted', 'downloading', 'download-waiting-lta', 'download-timeout-retry']).order_by('-small_name')
                    while piss_downloading.count() > 0:
                        print('[startProcessingLeakDetection UserChosenProcessImageSet]: Oh no. There are still images to be downloaded, waiting children to finish...')
                        time.sleep(10)
                        piss_downloading = worsica_api_models.UserChosenProcessImageSet.objects.filter(
                            user_chosen_product=user_chosen_product, processState__in=[
                                'submitted', 'downloading', 'download-waiting-lta', 'download-timeout-retry']).order_by('-small_name')
                    print('[startProcessingLeakDetection UserChosenProcessImageSet]: OK!')

                    lenImagesetOK = worsica_api_models.UserChosenProcessImageSet.objects.filter(user_chosen_product=user_chosen_product, processState='downloaded').count()
                    print(str(lenImagesetOK))
                    if lenImagesetOK == 0:  # lenImagesetError>0:
                        print('error-downloading')
                        _change_job_submission_state(user_chosen_product, 'error-downloading')
                        _change_leak_detection_state(leak_detection, 'error-downloading')
                        user_chosen_product.refresh_from_db()
                        user_chosen_product.managedByLeakDetectionId = None
                        user_chosen_product.save()
                    else:
                        print('downloaded')
                        _change_job_submission_state(user_chosen_product, 'downloaded')
                        _change_leak_detection_state(leak_detection, 'downloaded')
                        user_chosen_product.refresh_from_db()
                        user_chosen_product.managedByLeakDetectionId = None
                        user_chosen_product.save()
                except Exception as e:
                    print('[startProcessingLeakDetection UserChosenProcessImageSet]: Something went wrong at download. Abort!')
                    print(traceback.format_exc())
                    _change_job_submission_state(user_chosen_product, 'error-downloading')
                    _change_leak_detection_state(leak_detection, 'error-downloading')
                    user_chosen_product.refresh_from_db()
                    user_chosen_product.managedByLeakDetectionId = None
                    user_chosen_product.save()

            # 2- resample
            user_chosen_product = worsica_api_models.UserChosenProduct.objects.get(id=USER_CHOSEN_PRODUCT_ID)
            if (user_chosen_product.state in ['downloaded', 'error-resampling', 'converted']):
                PREV_JOB_SUBMISSION_STATE = user_chosen_product.state
                _change_job_submission_state(user_chosen_product, 'resampling')
                _change_leak_detection_state(leak_detection, 'resampling')
                piss = worsica_api_models.UserChosenProcessImageSet.objects.filter(
                    user_chosen_product=user_chosen_product, processState__in=[
                        'downloaded', 'error-resampling', 'converted']).order_by('-small_name')
                result = []
                try:
                    print('[startProcessingLeakDetection UserChosenProcessImageSet]: Start resampling...')
                    task_identifier = get_task_identifier_uc(user_chosen_product)
                    print(task_identifier)
                    for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                        print('[startProcessingLeakDetection UserChosenProcessImageSet]: Launch children ' + str(pw))
                        _tid = task_identifier + '-resampling-child' + str(pw)
                        result.append(
                            tasks.taskStartProcessingResampling.starmap(
                                [
                                    (pis.id,
                                     SERVICE,
                                     USER_ID,
                                     ROI_ID,
                                     USER_CHOSEN_PRODUCT_ID,
                                     PREV_JOB_SUBMISSION_STATE,
                                     GEOJSON_POLYGON,
                                     LOGS_FOLDER,
                                     LOG_FILENAME,
                                     True,
                                     req_host) for pis in piss if (
                                        pis.id %
                                        PARALLELISM_NUM_WORKERS) +
                                    1 == pw]).apply_async(
                                task_id=_tid))
                        time.sleep(2)  # wait 2 seconds for each async launches
                    piss_resampling = worsica_api_models.UserChosenProcessImageSet.objects.filter(user_chosen_product=user_chosen_product, processState__in=[
                        'downloaded', 'resampling', 'converted']).order_by('-small_name')
                    while piss_resampling.count() > 0:
                        print('[startProcessingLeakDetection UserChosenProcessImageSet]: There are still images to be resampled, waiting children to finish...')
                        time.sleep(10)
                        piss_resampling = worsica_api_models.UserChosenProcessImageSet.objects.filter(user_chosen_product=user_chosen_product, processState__in=[
                            'downloaded', 'resampling', 'converted']).order_by('-small_name')
                except Exception as e:
                    print(traceback.format_exc())
                    if len(result) > 0:
                        print('[startProcessingLeakDetection UserChosenProcessImageSet]: Revoking children to finish...')
                        for r in result:
                            r.revoke(terminate=True, signal='SIGKILL')

                lenImagesetOK = worsica_api_models.UserChosenProcessImageSet.objects.filter(user_chosen_product=user_chosen_product, processState='resampled').count()
                if lenImagesetOK == 0:  # lenImagesetError>0:
                    _change_job_submission_state(user_chosen_product, 'error-resampling')
                    _change_leak_detection_state(leak_detection, 'error-resampling')
                    user_chosen_product.refresh_from_db()
                    user_chosen_product.managedByLeakDetectionId = None
                    user_chosen_product.save()
                else:
                    _change_job_submission_state(user_chosen_product, 'resampled')
                    _change_leak_detection_state(leak_detection, 'resampled')
                    user_chosen_product.refresh_from_db()

            # 3- merge
            user_chosen_product = worsica_api_models.UserChosenProduct.objects.get(id=USER_CHOSEN_PRODUCT_ID)
            if (user_chosen_product.state in ['resampled', 'error-merging']):
                PREV_JOB_SUBMISSION_STATE = user_chosen_product.state
                _change_job_submission_state(user_chosen_product, 'merging')
                _change_leak_detection_state(leak_detection, 'merging')
                # note: 'submitted' for mergedPIS is 'resampled' on PIS
                mpiss = worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(user_chosen_product=user_chosen_product, processState__in=[
                    'submitted', 'error-merging']).order_by('-sensingDate')
                result = []
                try:
                    print('[startProcessingLeakDetection UserChosenMergedProcessImageSet]: Start merging...')
                    task_identifier = get_task_identifier_uc(user_chosen_product)
                    print(task_identifier)
                    for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                        print('[startProcessingLeakDetection UserChosenMergedProcessImageSet]: Launch children ' + str(pw))
                        _tid = task_identifier + '-merging-child' + str(pw)
                        result.append(
                            tasks.taskStartProcessingMerge.starmap(
                                [
                                    (mpis.id,
                                     SERVICE,
                                     USER_ID,
                                     ROI_ID,
                                     USER_CHOSEN_PRODUCT_ID,
                                     PREV_JOB_SUBMISSION_STATE,
                                     LOGS_FOLDER,
                                     LOG_FILENAME,
                                     True,
                                     req_host) for mpis in mpiss if (
                                        mpis.id %
                                        PARALLELISM_NUM_WORKERS) +
                                    1 == pw]).apply_async(
                                task_id=_tid))
                        time.sleep(2)  # wait 2 seconds for each async launches
                    mpiss_merging = worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(
                        user_chosen_product=user_chosen_product, processState__in=['submitted', 'merging']).order_by('-sensingDate')
                    while mpiss_merging.count() > 0:
                        print('[startProcessingLeakDetection UserChosenMergedProcessImageSet]: There are still images to be merged, waiting children to finish...')
                        time.sleep(10)
                        mpiss_merging = worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(
                            user_chosen_product=user_chosen_product, processState__in=['submitted', 'merging']).order_by('-sensingDate')
                except Exception as e:
                    print(traceback.format_exc())
                    if len(result) > 0:
                        print('[startProcessingLeakDetection UserChosenMergedProcessImageSet]: Revoking children to finish...')
                        for r in result:
                            r.revoke(terminate=True, signal='SIGKILL')

                lenImagesetOK = worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(user_chosen_product=user_chosen_product, processState='merged').count()
                if lenImagesetOK == 0:  # lenImagesetError>0:
                    _change_job_submission_state(user_chosen_product, 'error-merging')
                    _change_leak_detection_state(leak_detection, 'error-merging')
                    user_chosen_product.refresh_from_db()
                    user_chosen_product.managedByLeakDetectionId = None
                    user_chosen_product.save()
                else:
                    _change_job_submission_state(user_chosen_product, 'merged')
                    _change_leak_detection_state(leak_detection, 'merged')
                    user_chosen_product.refresh_from_db()

            # 4- process
            user_chosen_product = worsica_api_models.UserChosenProduct.objects.get(id=USER_CHOSEN_PRODUCT_ID)
            if (user_chosen_product.state in ['merged', 'error-processing']):
                PREV_JOB_SUBMISSION_STATE = user_chosen_product.state
                _change_job_submission_state(user_chosen_product, 'processing')
                _change_leak_detection_state(leak_detection, 'processing')
                mpiss = worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(user_chosen_product=user_chosen_product, processState__in=[
                    'merged', 'error-processing']).order_by('-sensingDate')
                result = []
                try:
                    print('[startProcessingLeakDetection UserChosenMergedProcessImageSet]: Start processing...')
                    task_identifier = get_task_identifier_uc(user_chosen_product)
                    print(task_identifier)
                    for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                        print('[startProcessingLeakDetection UserChosenMergedProcessImageSet]: Launch children ' + str(pw))
                        _tid = task_identifier + '-processing-child' + str(pw)
                        result.append(
                            tasks.taskStartProcessingProcess.starmap(
                                [
                                    (mpis.id,
                                     SERVICE,
                                     USER_ID,
                                     ROI_ID,
                                     USER_CHOSEN_PRODUCT_ID,
                                     PREV_JOB_SUBMISSION_STATE,
                                     BATH_VALUE,
                                     TOPO_VALUE,
                                     WI_THRESHOLD,
                                     WATER_INDEX,
                                     LOGS_FOLDER,
                                     LOG_FILENAME,
                                     True,
                                     req_host) for mpis in mpiss if (
                                        mpis.id %
                                        PARALLELISM_NUM_WORKERS) +
                                    1 == pw]).apply_async(
                                task_id=_tid))
                        time.sleep(2)  # wait 2 seconds for each async launches
                    mpiss_processing = worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(
                        user_chosen_product=user_chosen_product, processState__in=['merged', 'processing']).order_by('-sensingDate')
                    while mpiss_processing.count() > 0:
                        print('[startProcessingLeakDetection UserChosenMergedProcessImageSet]: There are still images to be processed, waiting children to finish...')
                        time.sleep(10)
                        mpiss_processing = worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(
                            user_chosen_product=user_chosen_product, processState__in=['merged', 'processing']).order_by('-sensingDate')
                    mpiss = worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(
                        user_chosen_product=user_chosen_product, processState__in=[
                            'processed', 'error-storing-processing']).order_by('-sensingDate')
                    print('[startProcessingLeakDetection UserChosenMergedProcessImageSet]: Start storing processing...')
                    task_identifier = get_task_identifier_uc(user_chosen_product)
                    print(task_identifier)
                    for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                        print('[startProcessingLeakDetection UserChosenMergedProcessImageSet]: Launch children ' + str(pw))
                        _tid = task_identifier + '-storing-processing-child' + str(pw)
                        _list_mpiss = [
                            (mpis.id,
                             SERVICE,
                             USER_ID,
                             ROI_ID,
                             USER_CHOSEN_PRODUCT_ID,
                             PREV_JOB_SUBMISSION_STATE,
                             WATER_INDEX,
                             False,
                             True,
                             req_host) for mpis in mpiss if (
                                mpis.id %
                                PARALLELISM_NUM_WORKERS) +
                            1 == pw]
                        result.append(tasks.taskStartProcessingStore.starmap(_list_mpiss).apply_async(task_id=_tid))
                        time.sleep(2)  # wait 2 seconds for each async launches
                    mpiss_storing = worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(user_chosen_product=user_chosen_product, processState__in=[
                        'processed', 'storing-processing']).order_by('-sensingDate')
                    while mpiss_storing.count() > 0:  # or ampiss_storing.count()>0:
                        print('[startProcessingLeakDetection UserChosenMergedProcessImageSet]: There are still merged or climatology images to be stored, waiting children to finish...')
                        print('[startProcessingLeakDetection UserChosenMergedProcessImageSet]: debug mpiss_storing=' + str(mpiss_storing.count()))
                        time.sleep(10)
                        mpiss_storing = worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(user_chosen_product=user_chosen_product, processState__in=[
                            'processed', 'storing-processing']).order_by('-sensingDate')
                except Exception as e:
                    print(traceback.format_exc())
                    if len(result) > 0:
                        print('[startProcessingLeakDetection UserChosenMergedProcessImageSet]: Revoking children to finish...')
                        for r in result:
                            r.revoke(terminate=True, signal='SIGKILL')
                lenImagesetOK = worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(user_chosen_product=user_chosen_product, processState='success').count()
                if lenImagesetOK == 0:  # lenImagesetError>0:
                    _change_job_submission_state(user_chosen_product, 'error-processing')
                    _change_leak_detection_state(leak_detection, 'error-processing')
                    user_chosen_product.refresh_from_db()
                    user_chosen_product.managedByLeakDetectionId = None
                    user_chosen_product.save()
                else:
                    _change_job_submission_state(user_chosen_product, 'processed')
                    _change_leak_detection_state(leak_detection, 'processed')
                    user_chosen_product.refresh_from_db()

            # 5- store
            user_chosen_product = worsica_api_models.UserChosenProduct.objects.get(id=USER_CHOSEN_PRODUCT_ID)
            if (user_chosen_product.state in ['processed']):
                _change_job_submission_state(user_chosen_product, 'stored')
                _change_leak_detection_state(leak_detection, 'stored')
                user_chosen_product.refresh_from_db()
                user_chosen_product.managedByLeakDetectionId = None
                user_chosen_product.save()
        else:
            # try to solve concurrency issues
            # if this product is being held by another process, must wait.
            # just 'follow' the user chosen product state until it crashes
            while (user_chosen_product.state not in ['error-downloading', 'error-download-corrupt', 'error-resampling', 'error-merging', 'error-processing', 'error-storing', 'stored']):
                _change_leak_detection_state(leak_detection, user_chosen_product.state)
                worsica_logger.info(
                    '[startProcessingLeakDetection]: the image ' +
                    user_chosen_product.name +
                    ' is being used by another leak detection (state ' +
                    user_chosen_product.state +
                    '), we need to wait 10 secs')
                user_chosen_product = worsica_api_models.UserChosenProduct.objects.get(id=USER_CHOSEN_PRODUCT_ID)
                time.sleep(10)  # wait 10 secs
            user_chosen_product = worsica_api_models.UserChosenProduct.objects.get(id=USER_CHOSEN_PRODUCT_ID)
            worsica_logger.info('[startProcessingLeakDetection]: the image ' + user_chosen_product.name + ' finished with state ' + user_chosen_product.state + '')
            _change_leak_detection_state(leak_detection, user_chosen_product.state)

    # anomaly
    mpiss = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(id=leak_detection_id,
                                                                                    jobSubmission__id=job_submission_id)  # start_processing_second_deriv_from = LD_SECOND_DERIV_FROM)
    leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(id=leak_detection_id, jobSubmission__id=job_submission_id)
    print('====ANOMALY====')
    print('LD_SECOND_DERIV_FROM = ' + LD_SECOND_DERIV_FROM)
    print('state = ' + leak_detection.processState)
    if (LD_SECOND_DERIV_FROM == 'by_anomaly' and leak_detection.processState in ['submitted', 'stored', 'error-calculating-diff', 'error-storing-calculated-diff']):
        result = []
        PREV_JOB_SUBMISSION_STATE = job_submission.state
        print('====RUN ANOMALY====')
        try:
            print('[startProcessingLeakDetection MergedProcessImageSetForLeakDetection]: Start calculating anomaly...')
            for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                print('[startProcessingLeakDetection MergedProcessImageSetForLeakDetection]: Launch children ' + str(pw))
                _tid = task_identifier + '-calculating-diff-child' + str(pw)
                result.append(
                    tasks.taskStartProcessingLeakDetectionAnomaly.starmap(
                        [
                            (mpis.id,
                             SERVICE,
                             USER_ID,
                             ROI_ID,
                             SIMULATION_ID,
                             PREV_JOB_SUBMISSION_STATE,
                             LOGS_FOLDER,
                             LOG_FILENAME,
                             req_host) for mpis in mpiss if (
                                mpis.id %
                                PARALLELISM_NUM_WORKERS) +
                            1 == pw]).apply_async(
                        task_id=_tid))
                time.sleep(2)  # wait 2 seconds for each async launches, to avoid race conditions at the beginning
            mpiss_calculating_diff = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(
                id=leak_detection_id, jobSubmission__id=job_submission_id, processState__in=[
                    'submitted', 'calculating-diff', 'calculated-diff', 'storing-calculated-diff']).order_by('-small_name')
            while mpiss_calculating_diff.count() > 0:
                print('[startProcessingLeakDetection MergedProcessImageSetForLeakDetection]: There are still images to be calculated by anomaly, waiting children to finish...')
                time.sleep(10)
                mpiss_calculating_diff = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(
                    id=leak_detection_id, jobSubmission__id=job_submission_id, processState__in=[
                        'submitted', 'calculating-diff', 'calculated-diff', 'storing-calculated-diff']).order_by('-small_name')
        except Exception as e:
            print(traceback.format_exc())
            if len(result) > 0:
                print('[startProcessingLeakDetection MergedProcessImageSetForLeakDetection]: Revoking children to finish...')
                for r in result:
                    r.revoke(terminate=True, signal='SIGKILL')

    # 2nd deriv
    mpiss = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(id=leak_detection_id,
                                                                                    jobSubmission__id=job_submission_id)  # start_processing_second_deriv_from = LD_SECOND_DERIV_FROM)
    leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(id=leak_detection_id, jobSubmission__id=job_submission_id)
    print('====2nd DERIV====')
    print('LD_SECOND_DERIV_FROM = ' + LD_SECOND_DERIV_FROM)
    print('state = ' + leak_detection.processState)
    if ((LD_SECOND_DERIV_FROM == 'by_anomaly' and leak_detection.processState in ['stored-calculated-diff'])
        or (LD_SECOND_DERIV_FROM == 'by_index' and leak_detection.processState in ['submitted', 'stored'])
            or leak_detection.processState in ['error-calculating-leak', 'error-storing-calculated-leak']):
        result = []
        PREV_JOB_SUBMISSION_STATE = job_submission.state
        print('====RUN 2nd DERIV====')
        try:
            print('[startProcessingLeakDetection MergedProcessImageSetForLeakDetection]: Start calculating 2nd derivative...')
            for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                print('[startProcessingLeakDetection MergedProcessImageSetForLeakDetection]: Launch children ' + str(pw))
                _tid = task_identifier + '-calculating-leak-child' + str(pw)
                result.append(
                    tasks.taskStartProcessingLeakDetectionSecondDeriv.starmap(
                        [
                            (mpis.id,
                             SERVICE,
                             USER_ID,
                             ROI_ID,
                             SIMULATION_ID,
                             PREV_JOB_SUBMISSION_STATE,
                             LOGS_FOLDER,
                             LOG_FILENAME,
                             req_host) for mpis in mpiss if (
                                mpis.id %
                                PARALLELISM_NUM_WORKERS) +
                            1 == pw]).apply_async(
                        task_id=_tid))
                time.sleep(2)  # wait 2 seconds for each async launches, to avoid race conditions at the beginning
            mpiss_calculating_leak = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(
                id=leak_detection_id, jobSubmission__id=job_submission_id, processState__in=[
                    'submitted', 'stored-calculated-diff', 'calculating-leak', 'calculated-leak', 'storing-calculated-leak']).order_by('-small_name')
            while mpiss_calculating_leak.count() > 0:
                print('[startProcessingLeakDetection MergedProcessImageSetForLeakDetection]: There are still images to be calculated to 2nd derivative, waiting children to finish...')
                time.sleep(10)
                mpiss_calculating_leak = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(
                    id=leak_detection_id, jobSubmission__id=job_submission_id, processState__in=[
                        'submitted', 'stored-calculated-diff', 'calculating-leak', 'calculated-leak', 'storing-calculated-leak']).order_by('-small_name')
        except Exception as e:
            print(traceback.format_exc())
            if len(result) > 0:
                print('[startProcessingLeakDetection MergedProcessImageSetForLeakDetection]: Revoking children to finish...')
                for r in result:
                    r.revoke(terminate=True, signal='SIGKILL')
    print('====FINISH====')
    print('LD_SECOND_DERIV_FROM = ' + LD_SECOND_DERIV_FROM)
    print('state = ' + leak_detection.processState)

    leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(id=leak_detection_id, jobSubmission__id=job_submission_id)
    worsica_api_utils.notify_leakdetection_processing_state(leak_detection)

    return HttpResponse(json.dumps({'simulation_id': job_submission.simulation_id, "job_submission_id": job_submission.id,
                                    "leak_detection_id": leak_detection.id, 'state': leak_detection.processState}), content_type='application/json')


@csrf_exempt
def run_job_submission_leak_detection_identify_leaks(request, job_submission_id, leak_detection_id):
    _host = request.get_host()
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = roi_id)
        # throw error for missing masks/pipe networks if its running cleanup leaks
        merged_imageset_ld = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(id=leak_detection_id)
    except Exception as e:
        print(traceback.format_exc())
        pass

    try:
        worsica_logger.info('[run_job_submission_leak_detection]: run id=' + str(job_submission_id) + ' leak_detection_id=' + str(leak_detection_id) + ' state=' + job_submission.state)
        task_identifier = get_task_identifier(job_submission) + '-ld' + str(leak_detection_id)  # +'-'+determine_leak_by
        print(task_identifier)
        tasks.taskStartProcessingLeakDetection2.apply_async(args=[job_submission.aos_id, job_submission.id, leak_detection_id, _host], task_id=task_identifier)
        return HttpResponse(json.dumps({'state': 'created', 'roi_id': job_submission.aos_id, 'id': job_submission.id, 'leak_detection_id': leak_detection_id}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'state': 'error', 'roi_id': job_submission.aos_id, 'id': job_submission.id,
                                        'leak_detection_id': leak_detection_id, 'error': str(e)}), content_type='application/json')
# Part 2: (Mask)+Identify leaks


def startProcessingLeakDetection2(roi_id, job_submission_id, leak_detection_id, req_host):
    job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, aos_id=roi_id, isVisible=True)
    job_submission_exec = json.loads(job_submission.exec_arguments)
    WATER_INDEX = str(job_submission_exec['detection']['waterIndex'])  # keep this one for the case of new image

    PROVIDER = job_submission.provider
    SERVICE = job_submission.service
    USER_ID = str(job_submission.user_id)
    ROI_ID = str(job_submission.aos_id)
    SIMULATION_ID = str(job_submission.simulation_id)
    JOB_SUBMISSION_NAME = job_submission.name.encode('ascii', 'ignore').decode('ascii')

    leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(id=leak_detection_id, jobSubmission__id=job_submission_id)
    leak_detection_exec = json.loads(leak_detection.exec_arguments)
    LD_WATER_INDEX = leak_detection.indexType  # this one is to assume the water index we really want to process!
    LD_IMAGE_SOURCE_FROM = leak_detection.image_source_from
    LD_SECOND_DERIV_FROM = leak_detection.start_processing_second_deriv_from  # determine_leak_by

    WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
    PATH_TO_PRODUCTS = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/' + WORKSPACE_SIMULATION_FOLDER
    NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER

    worsica_logger.info('[startProcessingLeakDetection]: ' + JOB_SUBMISSION_NAME + ' ' + SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) +
                        '-simulation' + str(SIMULATION_ID) + '-ld' + str(leak_detection_id) + '-' + LD_SECOND_DERIV_FROM + ' -- ' + job_submission.state)
    timestamp = datetime.datetime.today().strftime('%Y%m%d-%H%M%S')  # job_submission.lastRunAt.strftime('%Y%m%d-%H%M%S') #

    LOGS_FOLDER = './log'
    LOG_FILENAME = str(timestamp) + '-worsica-processing-service-' + SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + \
        '-simulation' + str(SIMULATION_ID) + '-ld' + str(leak_detection_id)  # +LD_SECOND_DERIV_FROM

    # try:
    mpiss = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(id=leak_detection_id,
                                                                                    jobSubmission__id=job_submission_id)  # start_processing_second_deriv_from = LD_SECOND_DERIV_FROM)
    task_identifier = get_task_identifier(job_submission) + '-ld' + str(leak_detection_id)  # +'-'+LD_SECOND_DERIV_FROM
    print(task_identifier)

    # identify leaks
    FILTER_BY_MASK = leak_detection.filter_by_mask
    # generate mask
    # create mask/filtering
    print('=============generate mask============')
    print('FILTER_BY_MASK = ' + str(FILTER_BY_MASK))
    print('state = ' + leak_detection.processState)
    if (FILTER_BY_MASK and leak_detection.processState in ['stored-determinated-leak', 'stored-calculated-leak',
                                                           'error-generating-mask-leak']):  # 'error-determinating-leak','stored-determinated-leak','error-storing-determinated-leak'
        print('=============start generate mask============')
        result = []
        PREV_JOB_SUBMISSION_STATE = job_submission.state
        try:
            print('[startProcessing MergedProcessImageSetForLeakDetection]: Start generating mask leaks...')
            for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                print('[startProcessing MergedProcessImageSetForLeakDetection]: Launch children ' + str(pw))
                _tid = task_identifier + '-filtering-leak-child' + str(pw)
                result.append(
                    tasks.taskStartProcessingLeakDetectionGenerateMask.starmap(
                        [
                            (mpis.id,
                             SERVICE,
                             USER_ID,
                             ROI_ID,
                             SIMULATION_ID,
                             PREV_JOB_SUBMISSION_STATE,
                             LOGS_FOLDER,
                             LOG_FILENAME,
                             req_host) for mpis in mpiss if (
                                mpis.id %
                                PARALLELISM_NUM_WORKERS) +
                            1 == pw]).apply_async(
                        task_id=_tid))
                time.sleep(2)  # wait 2 seconds for each async launches, to avoid race conditions at the beginning
            mpiss_filtering_leak = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(id=leak_detection_id, jobSubmission__id=job_submission_id, processState__in=[
                'stored-calculated-leak', 'generating-mask-leak']).order_by('-small_name')
            while mpiss_filtering_leak.count() > 0:
                print('[startProcessing MergedProcessImageSetForLeakDetection]: There are still images to filter leaks, waiting children to finish...')
                time.sleep(10)
                mpiss_filtering_leak = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(id=leak_detection_id, jobSubmission__id=job_submission_id, processState__in=[
                    'stored-calculated-leak', 'generating-mask-leak']).order_by('-small_name')
        except Exception as e:
            print(traceback.format_exc())
            if len(result) > 0:
                print('[startProcessing MergedProcessImageSetForLeakDetection]: Revoking children to finish...')
                for r in result:
                    r.revoke(terminate=True, signal='SIGKILL')
        # pass

    mpiss = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(id=leak_detection_id,
                                                                                    jobSubmission__id=job_submission_id)  # start_processing_second_deriv_from = LD_SECOND_DERIV_FROM)
    leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(id=leak_detection_id, jobSubmission__id=job_submission_id)
    print('=============identify leaks============')
    print('FILTER_BY_MASK = ' + str(FILTER_BY_MASK))
    print('state = ' + leak_detection.processState)
    if (leak_detection.processState in ['stored-determinated-leak', 'stored-calculated-leak', 'generated-mask-leak', 'error-determinating-leak', 'error-storing-determinated-leak']):
        print('=============start identify leaks============')
        result = []
        PREV_JOB_SUBMISSION_STATE = job_submission.state
        try:
            print('[startProcessing MergedProcessImageSetForLeakDetection]: Start identifying leaks...')
            for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                print('[startProcessing MergedProcessImageSetForLeakDetection]: Launch children ' + str(pw))
                _tid = task_identifier + '-identifying-leak-child' + str(pw)
                result.append(
                    tasks.taskStartProcessingLeakDetectionIdentifyLeaks.starmap(
                        [
                            (mpis.id,
                             SERVICE,
                             USER_ID,
                             ROI_ID,
                             SIMULATION_ID,
                             PREV_JOB_SUBMISSION_STATE,
                             LOGS_FOLDER,
                             LOG_FILENAME,
                             req_host) for mpis in mpiss if (
                                mpis.id %
                                PARALLELISM_NUM_WORKERS) +
                            1 == pw]).apply_async(
                        task_id=_tid))
                time.sleep(2)  # wait 2 seconds for each async launches, to avoid race conditions at the beginning
            mpiss_identifying_leak = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(
                id=leak_detection_id, jobSubmission__id=job_submission_id, processState__in=[
                    'stored-calculated-leak', 'storing-determinated-leak', 'determinating-leak']).order_by('-small_name')
            while mpiss_identifying_leak.count() > 0:
                print('[startProcessing MergedProcessImageSetForLeakDetection]: There are still images to identify leaks, waiting children to finish...')
                time.sleep(10)
                mpiss_identifying_leak = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(
                    id=leak_detection_id, jobSubmission__id=job_submission_id, processState__in=[
                        'stored-calculated-leak', 'storing-determinated-leak', 'determinating-leak']).order_by('-small_name')
        except Exception as e:
            print(traceback.format_exc())
            if len(result) > 0:
                print('[startProcessing MergedProcessImageSetForLeakDetection]: Revoking children to finish...')
                for r in result:
                    r.revoke(terminate=True, signal='SIGKILL')

        leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(id=leak_detection_id, jobSubmission__id=job_submission_id)
        worsica_api_utils.notify_leakdetection_processing_state(leak_detection)


@csrf_exempt
def run_job_submission(request, job_submission_id):
    _host = request.get_host()
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = roi_id)
        job_submission.lastRunAt = datetime.datetime.now()
        job_submission.lastFinishedAt = None
        job_submission.save()
        worsica_logger.info('[run_job_submission]: run id=' + str(job_submission_id) + ' state=' + job_submission.state)
        task_identifier = get_task_identifier(job_submission)
        print(task_identifier)
        tasks.taskStartProcessing.apply_async(args=[job_submission.aos_id, job_submission.id, _host], task_id=task_identifier)  # delay(job_submission.aos_id, job_submission.id, _host)
        return HttpResponse(json.dumps({'state': 'created', 'roi_id': job_submission.aos_id, 'id': job_submission.id}), content_type='application/json')
    except Exception as e:
        return HttpResponse(json.dumps({'state': 'error', 'roi_id': job_submission.aos_id, 'id': job_submission.id, 'error': str(e)}), content_type='application/json')


def startProcessing(roi_id, job_submission_id, req_host):
    os.chdir(ACTUAL_PATH)  # change to this path
    worsica_logger.info('[startProcessing]: ' + os.getcwd())

    job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, aos_id=roi_id, isVisible=True)
    job_submission_exec = json.loads(job_submission.exec_arguments)
    imagesets = job_submission_exec['inputs']['listOfImagesets']
    BATH_VALUE = str(job_submission_exec['detection']['bathDepth'])
    TOPO_VALUE = str(job_submission_exec['detection']['topoHeight'])
    WATER_INDEX = str(job_submission_exec['detection']['waterIndex'])
    WI_THRESHOLD = str(job_submission_exec['detection']['wiThreshold'])
    GEOJSON_POLYGON = '\"' + job_submission_exec['roi'] + '\"'

    PROVIDER = job_submission.provider
    SERVICE = job_submission.service
    USER_ID = str(job_submission.user_id)
    ROI_ID = str(job_submission.aos_id)
    SIMULATION_ID = str(job_submission.simulation_id)
    JOB_SUBMISSION_NAME = job_submission.name.encode('ascii', 'ignore').decode('ascii')

    WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
    PATH_TO_PRODUCTS = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/' + WORKSPACE_SIMULATION_FOLDER
    NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER

    worsica_logger.info('[startProcessing]: ' + JOB_SUBMISSION_NAME + ' ' + SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-simulation' + str(SIMULATION_ID) + ' -- ' + job_submission.state)
    timestamp = datetime.datetime.today().strftime('%Y%m%d-%H%M%S')  # job_submission.lastRunAt.strftime('%Y%m%d-%H%M%S') #

    LOGS_FOLDER = './log'
    LOG_FILENAME = str(timestamp) + '-worsica-processing-service-' + SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-simulation' + str(SIMULATION_ID)

    if (job_submission.state in ['submitted', 'error-downloading', 'error-download-corrupt']):
        PREV_JOB_SUBMISSION_STATE = job_submission.state
        _change_job_submission_state(job_submission, 'downloading')

        # create repositoryimagesets, processimagesets and mergedprocessimagesets
        for imageset in imagesets:
            UUID = imageset['uuid']
            IMAGESET_NAME = imageset['name']  # imageset['reference']
            SIMULATION_NAME = imageset['name']
            if PROVIDER == 'drone':
                # fetch from userrepositoryimagesets
                user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=USER_ID)
                dis = worsica_api_models.UserRepositoryImageSets.objects.get(user_repository=user_repository, name=IMAGESET_NAME, uuid=str(UUID))
                # create processimageset with uploaded imageset
                pis, pis_created = worsica_api_models.ProcessImageSet.objects.get_or_create(jobSubmission=job_submission, uploadedimageSet=dis)
                pis.name = dis.name + '_' + dis.issueDate.strftime('%Y%m%dT%H%M%S')
            else:
                # we need to handle the concurrency during downloads.
                # If 2 users download both the same file and download script is not ready to handle such concurrency, we need to handle that as a dijkstra semaphore,
                # in order to not compromise the processing.
                # check an entry for downloaded repository imageset
                dis, dis_created = worsica_api_models.RepositoryImageSets.objects.get_or_create(name=IMAGESET_NAME, uuid=str(UUID))
                if dis_created:  # if doesnt exist, create
                    worsica_logger.info('[startProcessing RepositoryImageSets]: add imageset ' + IMAGESET_NAME + ' to the RepositoryImageSets ')
                    dis.filePath = nextcloud_access.NEXTCLOUD_URL_PATH + '/repository_images/' + IMAGESET_NAME + '.zip'
                    # dis.managedByJobId='simulation'+str(job_submission.id)
                    dis.save()
                else:
                    worsica_logger.info('[startProcessing RepositoryImageSets]: imageset ' + IMAGESET_NAME + ' already exists on the RepositoryImageSets')
                    if dis.state in ['expired']:
                        worsica_logger.info('[startProcessingLeakDetection RepositoryImageSets]: imageset ' + IMAGESET_NAME + ' expired, set submitted and update dates')
                        _change_repository_imageset_dates(dis)
                        dis.managedByJobId = None
                        dis.state = 'submitted'
                        dis.save()
                # create processimageset
                pis, pis_created = worsica_api_models.ProcessImageSet.objects.get_or_create(jobSubmission=job_submission, imageSet=dis)
                pis.name = dis.name
            pis.small_name = imageset['small_name']
            pis.reference = dis.reference
            pis.uuid = dis.uuid
            pis.filePath = dis.filePath
            # for water leak, do NOT start again from downloaded state
            # must remain its actual state
            if SERVICE != 'waterleak':
                if PROVIDER == 'drone':
                    pis.processState = 'uploaded'
                else:
                    pis.processState = ('submitted' if dis.state == 'expired' else dis.state)
            pis.convertToL2A = imageset['convertToL2A']
            pis.save()
            if pis_created:  # if doesnt exist, create
                worsica_logger.info('[startProcessing ProcessImageSet]: add imageset ' + IMAGESET_NAME + ' to the ProcessImageSet of the JobSubmission')
            else:
                worsica_logger.info('[startProcessing ProcessImageSet]: imageset ' + IMAGESET_NAME + ' already exists on the ProcessImageSet of the JobSubmission')

        # create mergedprocessimageset
        piss = worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission)
        for pis in piss:  # wipe procimageset ids
            timestamp = pis.name.split('_')[2]
            timestamp_split = timestamp.split('T')
            date = timestamp_split[0] + '_' + timestamp_split[1]
            name = 'merged_resampled_' + str(date)
            mpis, mpis_created = worsica_api_models.MergedProcessImageSet.objects.get_or_create(jobSubmission=job_submission, name=name)
            sd = datetime.datetime.strptime(timestamp, '%Y%m%dT%H%M%S')
            mpis.small_name = 'Merged images of ' + str(sd)
            mpis.sensingDate = sd
            mpis.procImageSet_ids = ''
            mpis.save()
        for pis in piss:  # rebuild procimageset ids
            timestamp = pis.name.split('_')[2]
            timestamp_split = timestamp.split('T')
            date = timestamp_split[0] + '_' + timestamp_split[1]
            name = 'merged_resampled_' + str(date)
            mpis = worsica_api_models.MergedProcessImageSet.objects.get(jobSubmission=job_submission, name=name)
            mpis.procImageSet_ids += (',' if mpis.procImageSet_ids != '' else '') + str(pis.id)
            mpis.save()
            if PROVIDER == 'drone':
                pis.name = pis.name.replace('_' + timestamp, '')  # remove timestamp from processimageset filename
                pis.save()

        # FOR WATER LEAK
        if SERVICE == 'waterleak':
            mpiss = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission).order_by('sensingDate')
            leastrecentdate = mpiss.first().sensingDate
            mostrecentdate = mpiss.last().sensingDate
            dateseries = [mostrecentdate - datetime.timedelta(days=5 * y) for y in range(0, int((mostrecentdate - leastrecentdate).days / 5) + 1)]
            for x in range(0, len(dateseries)):
                date = dateseries[x].strftime("%Y-%m-%d")
                dateagg = dateseries[x].strftime("%Y%m%d")
                name = 'virtual_merged_resampled_' + str(dateagg) + '_000000'
                datesplit = date.split('-')
                dateyear, datemonth, dateday = datesplit[0], datesplit[1], datesplit[2]
                img = mpiss.filter(sensingDate__year=dateyear, sensingDate__month=datemonth, sensingDate__day=dateday,)
                imagesetExists = (len(img) > 0)
                if not imagesetExists:
                    vmpis, vmpis_created = worsica_api_models.MergedProcessImageSet.objects.get_or_create(jobSubmission=job_submission, name=name, isVirtual=True)
                    if vmpis_created:
                        sd = datetime.datetime.strptime(date, '%Y-%m-%d')
                        vmpis.small_name = 'Virtual Merged images of ' + str(sd)
                        vmpis.sensingDate = sd
                    vmpis.save()
            # build interpolation id lists for each merged product
            mpiss = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission).order_by('sensingDate')
            for mpis in mpiss:
                mpis.interpolationImageSet_ids = ''
                mpis.save()
            for mpis in mpiss:
                if mpis != mpiss.first() and mpis != mpiss.last():  # do not do interpolation on first and last images
                    TIMELINE_IMAGESET_DAYS = 10
                    # set interpolation ids for each merged/virtual merged image
                    startDate = mpis.sensingDate.strftime("%Y-%m-%d")
                    endDate = (mpis.sensingDate - datetime.timedelta(days=TIMELINE_IMAGESET_DAYS)).strftime("%Y-%m-%d")
                    mpiss_left = mpiss.filter(sensingDate__date__gte=endDate, sensingDate__date__lt=startDate).order_by('sensingDate')
                    for m in mpiss_left:
                        if m.id != mpis.id:  # please do not add its own id for interpolation
                            mpis.interpolationImageSet_ids += (',' if mpis.interpolationImageSet_ids != '' else '') + str(m.id)
                    endDate = (mpis.sensingDate + datetime.timedelta(days=TIMELINE_IMAGESET_DAYS)).strftime("%Y-%m-%d")
                    mpiss_right = mpiss.filter(sensingDate__date__gt=startDate, sensingDate__date__lte=endDate).order_by('sensingDate')
                    for m in mpiss_right:
                        if m.id != mpis.id:  # please do not add its own id for interpolation
                            mpis.interpolationImageSet_ids += (',' if mpis.interpolationImageSet_ids != '' else '') + str(m.id)
                    mpis.save()
            # create climatology(average)
            mpiss = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission).order_by('sensingDate')
            for mpis in mpiss:
                date = mpis.sensingDate.strftime("%Y-%m-%d")
                datesplit = date.split('-')
                dateyear, datemonth, dateday = datesplit[0], datesplit[1], datesplit[2]
                name = 'climatology_' + str(datemonth) + '' + str(dateday)
                clim, clim_created = worsica_api_models.AverageMergedProcessImageSet.objects.get_or_create(jobSubmission=job_submission, name=name)
                clim.small_name = 'Climatology of ' + str(datemonth) + '-' + str(dateday)
                clim.date = str(datemonth) + '-' + str(dateday)
                clim.mergedprocImageSet_ids = ''
                clim.save()
            for mpis in mpiss:
                date = mpis.sensingDate.strftime("%Y-%m-%d")
                datesplit = date.split('-')
                dateyear, datemonth, dateday = datesplit[0], datesplit[1], datesplit[2]
                name = 'climatology_' + str(datemonth) + '' + str(dateday)
                clim = worsica_api_models.AverageMergedProcessImageSet.objects.get(jobSubmission=job_submission, name=name)
                clim.mergedprocImageSet_ids += (',' if clim.mergedprocImageSet_ids != '' else '') + str(mpis.id)
                clim.save()

        # ---------------------------------------------------------
        # start now the workflow
        # 1- download/upload
        if PROVIDER == 'drone':
            _change_job_submission_state(job_submission, 'uploaded')

        else:  # (download only if sentinel2)
            piss = worsica_api_models.ProcessImageSet.objects.filter(
                jobSubmission=job_submission,
                processState__in=[
                    'submitted',
                    'error-downloading',
                    'error-download-corrupt',
                    'download-waiting-lta',
                    'download-timeout-retry']).order_by('-small_name')
            result = []
            try:
                print('[startProcessing ProcessImageSet]: Start downloading...')
                task_identifier = get_task_identifier(job_submission)
                print(task_identifier)
                for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                    print('[startProcessing ProcessImageSet]: Launch children ' + str(pw))
                    _tid = task_identifier + '-downloading-child' + str(pw)
                    result.append(
                        tasks.taskStartProcessingDownload.starmap(
                            [
                                (pis.id,
                                 SERVICE,
                                 USER_ID,
                                 ROI_ID,
                                 SIMULATION_ID,
                                 PREV_JOB_SUBMISSION_STATE,
                                 LOGS_FOLDER,
                                 LOG_FILENAME,
                                 (pis.id %
                                  PARALLELISM_NUM_WORKERS) +
                                 1,
                                 False,
                                 req_host) for pis in piss if (
                                    pis.id %
                                    PARALLELISM_NUM_WORKERS) +
                                1 == pw]).apply_async(
                            task_id=_tid))
                    time.sleep(2)  # wait 2 seconds for each async launches, to avoid race conditions at the beginning
                piss_downloading = worsica_api_models.ProcessImageSet.objects.filter(
                    jobSubmission=job_submission, processState__in=[
                        'submitted', 'downloading', 'download-waiting-lta', 'download-timeout-retry']).order_by('-small_name')
                while piss_downloading.count() > 0:
                    print('[startProcessing ProcessImageSet]: There are still images to be downloaded, waiting children to finish...')
                    time.sleep(10)
                    piss_downloading = worsica_api_models.ProcessImageSet.objects.filter(
                        jobSubmission=job_submission, processState__in=[
                            'submitted', 'downloading', 'download-waiting-lta', 'download-timeout-retry']).order_by('-small_name')
            except Exception as e:
                print(traceback.format_exc())
                if len(result) > 0:
                    print('[startProcessing ProcessImageSet]: Revoking children to finish...')
                    for r in result:
                        r.revoke(terminate=True, signal='SIGKILL')
            # TODO: on waterleak do not throw error on job submission for error-download-corrupt images
            # just continue
            print('[startProcessing ProcessImageSet]: Make sure there is no download retry due to error.')
            piss_downloading = worsica_api_models.ProcessImageSet.objects.filter(
                jobSubmission=job_submission,
                processState__in=[
                    'submitted',
                    'downloading',
                    'download-waiting-lta',
                    'download-timeout-retry']).order_by('-small_name')
            while piss_downloading.count() > 0:
                print('[startProcessing ProcessImageSet]: Oh no. There are still images to be downloaded, waiting children to finish...')
                time.sleep(10)
                piss_downloading = worsica_api_models.ProcessImageSet.objects.filter(
                    jobSubmission=job_submission, processState__in=[
                        'submitted', 'downloading', 'download-waiting-lta', 'download-timeout-retry']).order_by('-small_name')
            print('[startProcessing ProcessImageSet]: OK!')

            if (SERVICE == 'waterleak'):
                _change_job_submission_state(job_submission, 'downloaded')
            else:
                # relax
                _change_job_submission_state(job_submission, 'downloaded')

    # 2- does it require L2A correction?
    # check the number of imagesets with that flag
    REQUIRES_CONVERSION = (worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission, convertToL2A=True).count() > 0)
    if REQUIRES_CONVERSION:
        worsica_logger.info('[startProcessing RepositoryImageSets]: there are images that require conversion, checking them...')
        if (job_submission.state in ['downloaded', 'error-converting']):
            # create repositoryimagesets, processimagesets and mergedprocessimagesets
            piss = worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['downloaded', 'error-converting'], convertToL2A=True).order_by('-small_name')
            for imageset in piss:
                REQUIRES_CONVERSION = True  # trigger true if has images to convert
                UUID = imageset.uuid
                L1C_IMAGESET_NAME = imageset.name  # imageset['reference']
                L2A_IMAGESET_NAME = L1C_IMAGESET_NAME.replace('L1C', 'L2A')
                # we need to handle the concurrency during conversions.
                # If 2 users convert both the same file and download script is not ready to handle such concurrency, we need to handle that as a dijkstra semaphore,
                # in order to not compromise the processing.
                # check an entry for downloaded repository imageset
                dis, dis_created = worsica_api_models.RepositoryImageSets.objects.get_or_create(name=L2A_IMAGESET_NAME, uuid="(converted using Sen2Cor)")
                if dis_created:  # if doesnt exist, create
                    worsica_logger.info('[startProcessing RepositoryImageSets]: add imageset ' + L2A_IMAGESET_NAME + ' to the RepositoryImageSets ')
                    dis.filePath = nextcloud_access.NEXTCLOUD_URL_PATH + '/repository_images/' + L2A_IMAGESET_NAME + '.zip'
                    dis.save()
                else:
                    worsica_logger.info('[startProcessing RepositoryImageSets]: imageset ' + L2A_IMAGESET_NAME + ' already exists on the RepositoryImageSets, linking...')
                    if dis.state in ['expired']:
                        worsica_logger.info('[startProcessingLeakDetection RepositoryImageSets]: imageset ' + L2A_IMAGESET_NAME + ' expired, set submitted and update dates')
                        _change_repository_imageset_dates(dis)
                        dis.managedByJobId = None
                        dis.state = 'submitted'
                        dis.save()
                # link the child reference with parent
                parent_dis = worsica_api_models.RepositoryImageSets.objects.get(name=L1C_IMAGESET_NAME, uuid=str(UUID))
                parent_dis.childRepositoryImageSet = dis
                parent_dis.save()

            PREV_JOB_SUBMISSION_STATE = job_submission.state
            _change_job_submission_state(job_submission, 'converting')
            # ---------------------------------------------------------
            # start now the workflow
            piss = worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['downloaded', 'error-converting'], convertToL2A=True).order_by('-small_name')
            result = []
            CUSTOM_PARALLELISM_NUM_WORKERS = PARALLELISM_NUM_WORKERS if settings.ENABLE_GRID else 2
            try:
                print('[startProcessing ProcessImageSet]: Start converting to L2A...')
                task_identifier = get_task_identifier(job_submission)
                print(task_identifier)
                for pw in range(1, CUSTOM_PARALLELISM_NUM_WORKERS + 1):
                    # print([(pis.id, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, (pis.id%PARALLELISM_NUM_WORKERS)+1 ) for pis in piss if (pis.id%PARALLELISM_NUM_WORKERS)+1 == pw])
                    print('[startProcessing ProcessImageSet]: Launch children ' + str(pw))
                    _tid = task_identifier + '-converting-child' + str(pw)
                    result.append(
                        tasks.taskStartProcessingL2AConversion.starmap(
                            [
                                (pis.id,
                                 SERVICE,
                                 USER_ID,
                                 ROI_ID,
                                 SIMULATION_ID,
                                 PREV_JOB_SUBMISSION_STATE,
                                 LOGS_FOLDER,
                                 LOG_FILENAME,
                                 False,
                                 req_host) for pis in piss if (
                                    pis.id %
                                    CUSTOM_PARALLELISM_NUM_WORKERS) +
                                1 == pw]).apply_async(
                            task_id=_tid))
                    time.sleep(2)  # wait 2 seconds for each async launches, to avoid race conditions at the beginning
                piss_converting = worsica_api_models.ProcessImageSet.objects.filter(
                    jobSubmission=job_submission, processState__in=[
                        'downloaded', 'converting'], convertToL2A=True).order_by('-small_name')
                while piss_converting.count() > 0:
                    print('[startProcessing ProcessImageSet]: There are still images to be converted, waiting children to finish...')
                    time.sleep(10)
                    piss_converting = worsica_api_models.ProcessImageSet.objects.filter(
                        jobSubmission=job_submission, processState__in=[
                            'downloaded', 'converting'], convertToL2A=True).order_by('-small_name')
            except Exception as e:
                print(traceback.format_exc())
                if len(result) > 0:
                    print('[startProcessing ProcessImageSet]: Revoking children to finish...')
                    for r in result:
                        r.revoke(terminate=True, signal='SIGKILL')
            # TODO: on waterleak do not throw error on job submission for error-download-corrupt images
            # just continue
            print('[startProcessing ProcessImageSet]: Make sure there is no download retry due to error.')
            piss_converting = worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['downloaded', 'converting'], convertToL2A=True).order_by('-small_name')
            while piss_converting.count() > 0:
                print('[startProcessing ProcessImageSet]: Oh no. There are still images to be converted, waiting children to finish...')
                time.sleep(10)
                piss_converting = worsica_api_models.ProcessImageSet.objects.filter(
                    jobSubmission=job_submission, processState__in=[
                        'downloaded', 'converting'], convertToL2A=True).order_by('-small_name')
            print('[startProcessing ProcessImageSet]: OK!')

            if (SERVICE == 'waterleak'):
                _change_job_submission_state(job_submission, 'converted')
            else:
                # relax
                _change_job_submission_state(job_submission, 'converted')

    # 3-resampling
    if (job_submission.state in ['downloaded', 'uploaded', 'converted', 'error-resampling']):
        PREV_JOB_SUBMISSION_STATE = job_submission.state
        _change_job_submission_state(job_submission, 'resampling')
        print('[startProcessing ProcessImageSet]: Cooldown 20s to wait for possible pending images...')
        time.sleep(20)
        piss = worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['downloaded', 'uploaded', 'error-resampling', 'converted']).order_by('-small_name')
        result = []
        try:
            print('[startProcessing ProcessImageSet]: Start resampling...')
            task_identifier = get_task_identifier(job_submission)
            print(task_identifier)
            for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                print('[startProcessing ProcessImageSet]: Launch children ' + str(pw))
                _tid = task_identifier + '-resampling-child' + str(pw)
                result.append(
                    tasks.taskStartProcessingResampling.starmap(
                        [
                            (pis.id,
                             SERVICE,
                             USER_ID,
                             ROI_ID,
                             SIMULATION_ID,
                             PREV_JOB_SUBMISSION_STATE,
                             GEOJSON_POLYGON,
                             LOGS_FOLDER,
                             LOG_FILENAME,
                             False,
                             req_host) for pis in piss if (
                                pis.id %
                                PARALLELISM_NUM_WORKERS) +
                            1 == pw]).apply_async(
                        task_id=_tid))
                time.sleep(2)  # wait 2 seconds for each async launches
            piss_resampling = worsica_api_models.ProcessImageSet.objects.filter(
                jobSubmission=job_submission, processState__in=[
                    'downloaded', 'uploaded', 'resampling', 'converted']).order_by('-small_name')
            while piss_resampling.count() > 0:
                print('[startProcessing ProcessImageSet]: There are still images to be resampled, waiting children to finish...')
                time.sleep(10)
                piss_resampling = worsica_api_models.ProcessImageSet.objects.filter(
                    jobSubmission=job_submission, processState__in=[
                        'downloaded', 'uploaded', 'resampling', 'converted']).order_by('-small_name')
        except Exception as e:
            print(traceback.format_exc())
            if len(result) > 0:
                print('[startProcessing ProcessImageSet]: Revoking children to finish...')
                for r in result:
                    r.revoke(terminate=True, signal='SIGKILL')

        # TODO: on waterleak do not throw error on job submission for error-download-corrupt images
        # just continue
        if (SERVICE == 'waterleak'):
            _change_job_submission_state(job_submission, 'resampled')
        else:
            _change_job_submission_state(job_submission, 'resampled')

    # 4-merging
    if (job_submission.state in ['resampled', 'error-merging']):
        PREV_JOB_SUBMISSION_STATE = job_submission.state
        _change_job_submission_state(job_submission, 'merging')
        # note: 'submitted' for mergedPIS is 'resampled' on PIS
        mpiss = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['submitted', 'error-merging'], isVirtual=False).order_by('-sensingDate')
        result = []
        try:
            print('[startProcessing MergedProcessImageSet]: Start merging...')
            task_identifier = get_task_identifier(job_submission)
            print(task_identifier)
            for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                print('[startProcessing MergedProcessImageSet]: Launch children ' + str(pw))
                _tid = task_identifier + '-merging-child' + str(pw)
                print([(mpis.id, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, req_host)
                       for mpis in mpiss if (mpis.id % PARALLELISM_NUM_WORKERS) + 1 == pw])
                result.append(
                    tasks.taskStartProcessingMerge.starmap(
                        [
                            (mpis.id,
                             SERVICE,
                             USER_ID,
                             ROI_ID,
                             SIMULATION_ID,
                             PREV_JOB_SUBMISSION_STATE,
                             LOGS_FOLDER,
                             LOG_FILENAME,
                             False,
                             req_host) for mpis in mpiss if (
                                mpis.id %
                                PARALLELISM_NUM_WORKERS) +
                            1 == pw]).apply_async(
                        task_id=_tid))
                time.sleep(2)  # wait 2 seconds for each async launches
            mpiss_merging = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['submitted', 'merging'], isVirtual=False).order_by('-sensingDate')
            while mpiss_merging.count() > 0:
                print('[startProcessing MergedProcessImageSet]: There are still images to be merged, waiting children to finish...')
                time.sleep(10)
                mpiss_merging = worsica_api_models.MergedProcessImageSet.objects.filter(
                    jobSubmission=job_submission, processState__in=[
                        'submitted', 'merging'], isVirtual=False).order_by('-sensingDate')
            if (SERVICE != 'waterleak'):  # coastal/inland
                mpiss = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['merged', 'error-storing-rgb'], isVirtual=False).order_by('-sensingDate')
                print('[startProcessing MergedProcessImageSet]: Start storing rgb...')
                task_identifier = get_task_identifier(job_submission)
                print(task_identifier)
                for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                    print('[startProcessing MergedProcessImageSet]: Launch children ' + str(pw))
                    _tid = task_identifier + '-storing-rgb-child' + str(pw)
                    _list_mpiss = [
                        (mpis.id,
                         SERVICE,
                         USER_ID,
                         ROI_ID,
                         SIMULATION_ID,
                         PREV_JOB_SUBMISSION_STATE,
                         WATER_INDEX,
                         False,
                         False,
                         req_host) for mpis in mpiss if (
                            mpis.id %
                            PARALLELISM_NUM_WORKERS) +
                        1 == pw]
                    result.append(tasks.taskStartProcessingStore.starmap(_list_mpiss).apply_async(task_id=_tid))
                    time.sleep(2)  # wait 2 seconds for each async launches
                mpiss_storing = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['merged', 'storing-rgb']).order_by('-sensingDate')
                while mpiss_storing.count() > 0:  # or ampiss_storing.count()>0:
                    print('[startProcessing MergedProcessImageSet]: There are still merged or climatology images to be stored, waiting children to finish...')
                    print('[startProcessing MergedProcessImageSet]: debug mpiss_storing=' + str(mpiss_storing.count()))
                    time.sleep(10)
                    mpiss_storing = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['merged', 'storing-rgb']).order_by('-sensingDate')
        except Exception as e:
            print(traceback.format_exc())
            if len(result) > 0:
                print('[startProcessing MergedProcessImageSet]: Revoking children to finish...')
                for r in result:
                    r.revoke(terminate=True, signal='SIGKILL')
        # TODO: on waterleak do not throw error on job submission for error-download-corrupt images
        # just continue
        if (SERVICE == 'waterleak'):
            _change_job_submission_state(job_submission, 'merged')
        else:
            _change_job_submission_state(job_submission, 'merged')

    # ---------------waterleak only---------------
    if (SERVICE == 'waterleak'):
        # ---------------generate virtual-------------
        if (job_submission.state in ['merged', 'error-generating-virtual']):
            PREV_JOB_SUBMISSION_STATE = job_submission.state
            _change_job_submission_state(job_submission, 'generating-virtual')
            vmpiss = worsica_api_models.MergedProcessImageSet.objects.filter(
                jobSubmission=job_submission, processState__in=[
                    'submitted', 'error-generating-virtual'], isVirtual=True).order_by('-sensingDate')
            result = []
            try:
                print('[startProcessing VirtualMergedProcessImageSet]: Start generating virtual imagesets...')
                task_identifier = get_task_identifier(job_submission)
                print(task_identifier)
                # sample imageset is used to build virtual empty imagesets with information (geotransform, imagesize) from the sample
                # get the name
                SAMPLE_IMAGESET_NAME = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['merged'])[0].name
                SIMULATION_NAME2 = SAMPLE_IMAGESET_NAME
                for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                    print('[startProcessing VirtualMergedProcessImageSet]: Launch children ' + str(pw))
                    _tid = task_identifier + '-generating-virtual-img-child' + str(pw)
                    result.append(
                        tasks.taskStartGeneratingVirtualImagesProcess.starmap(
                            [
                                (vmpis.id,
                                 SERVICE,
                                 USER_ID,
                                 ROI_ID,
                                 SIMULATION_ID,
                                 PREV_JOB_SUBMISSION_STATE,
                                 LOGS_FOLDER,
                                 LOG_FILENAME,
                                 SAMPLE_IMAGESET_NAME,
                                 SIMULATION_NAME2,
                                 req_host) for vmpis in vmpiss if (
                                    vmpis.id %
                                    PARALLELISM_NUM_WORKERS) +
                                1 == pw]).apply_async(
                            task_id=_tid))
                    time.sleep(10)  # wait 10 seconds for each async launches
                vmpiss_generating = worsica_api_models.MergedProcessImageSet.objects.filter(
                    jobSubmission=job_submission, processState__in=[
                        'submitted', 'generating-virtual'], isVirtual=True).order_by('-sensingDate')
                while vmpiss_generating.count() > 0:
                    print('[startProcessing VirtualMergedProcessImageSet]: There are still images to be processed, waiting children to finish...')
                    time.sleep(10)
                    vmpiss_generating = worsica_api_models.MergedProcessImageSet.objects.filter(
                        jobSubmission=job_submission, processState__in=[
                            'submitted', 'generating-virtual'], isVirtual=True).order_by('-sensingDate')
            except Exception as e:
                print(traceback.format_exc())
                if len(result) > 0:
                    print('[startProcessing VirtualMergedProcessImageSet]: Revoking children to finish...')
                    for r in result:
                        r.revoke(terminate=True, signal='SIGKILL')
            # TODO: on waterleak do not throw error on job submission for error-download-corrupt images
            # just continue
            if (SERVICE == 'waterleak'):
                _change_job_submission_state(job_submission, 'generated-virtual')
            else:
                _change_job_submission_state(job_submission, 'generated-virtual')

        # ---------------interpolate (merge and virtual-merge)-------------
        if (job_submission.state in ['generated-virtual', 'error-interpolating-products']):
            PREV_JOB_SUBMISSION_STATE = job_submission.state
            _change_job_submission_state(job_submission, 'interpolating-products')
            mpiss = worsica_api_models.MergedProcessImageSet.objects.filter(
                jobSubmission=job_submission, processState__in=[
                    'merged', 'generated-virtual', 'error-interpolating-products']).order_by('-sensingDate')
            result = []
            CUSTOM_PARALLELISM_NUM_WORKERS = 1  # interpolation is a sequential operation
            try:
                print('[startProcessing MergedProcessImageSet]: Start interpolating imagesets...')
                task_identifier = get_task_identifier(job_submission)
                print(task_identifier)
                for pw in range(1, CUSTOM_PARALLELISM_NUM_WORKERS + 1):
                    print('[startProcessing MergedProcessImageSet]: Launch children ' + str(pw))
                    _tid = task_identifier + '-interpolating-products-child' + str(pw)
                    result.append(
                        tasks.taskStartInterpolatingProductsProcess.starmap(
                            [
                                (mpis.id,
                                 SERVICE,
                                 USER_ID,
                                 ROI_ID,
                                 SIMULATION_ID,
                                 PREV_JOB_SUBMISSION_STATE,
                                 LOGS_FOLDER,
                                 LOG_FILENAME,
                                 req_host) for mpis in mpiss if (
                                    mpis.id %
                                    CUSTOM_PARALLELISM_NUM_WORKERS) +
                                1 == pw]).apply_async(
                            task_id=_tid))
                    time.sleep(2)  # wait 2 seconds for each async launches
                mpiss_interpolating = worsica_api_models.MergedProcessImageSet.objects.filter(
                    jobSubmission=job_submission, processState__in=[
                        'merged', 'generated-virtual', 'interpolating-products']).order_by('-sensingDate')
                while mpiss_interpolating.count() > 0:
                    print('[startProcessing MergedProcessImageSet]: There are still images to be processed, waiting children to finish...')
                    time.sleep(10)
                    mpiss_interpolating = worsica_api_models.MergedProcessImageSet.objects.filter(
                        jobSubmission=job_submission, processState__in=[
                            'merged', 'generated-virtual', 'interpolating-products']).order_by('-sensingDate')
            except Exception as e:
                print(traceback.format_exc())
                if len(result) > 0:
                    print('[startProcessing MergedProcessImageSet]: Revoking children to finish...')
                    for r in result:
                        r.revoke(terminate=True, signal='SIGKILL')
            # TODO: on waterleak do not throw error on job submission for error-download-corrupt images
            # just continue
            if (SERVICE == 'waterleak'):
                _change_job_submission_state(job_submission, 'interpolated-products')
            else:
                _change_job_submission_state(job_submission, 'interpolated-products')

    # --------------------------------------------

    # 5- processing
    if (job_submission.state in ['merged', 'interpolated-products', 'error-processing']):
        PREV_JOB_SUBMISSION_STATE = job_submission.state
        _change_job_submission_state(job_submission, 'processing')
        mpiss = worsica_api_models.MergedProcessImageSet.objects.filter(
            jobSubmission=job_submission, processState__in=[
                'merged', 'stored-rgb', 'interpolated-products', 'error-processing']).order_by('-sensingDate')
        result = []
        try:
            print('[startProcessing MergedProcessImageSet]: Start processing...')
            task_identifier = get_task_identifier(job_submission)
            print(task_identifier)
            for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                print('[startProcessing MergedProcessImageSet]: Launch children ' + str(pw))
                _tid = task_identifier + '-processing-child' + str(pw)
                result.append(
                    tasks.taskStartProcessingProcess.starmap(
                        [
                            (mpis.id,
                             SERVICE,
                             USER_ID,
                             ROI_ID,
                             SIMULATION_ID,
                             PREV_JOB_SUBMISSION_STATE,
                             BATH_VALUE,
                             TOPO_VALUE,
                             WI_THRESHOLD,
                             WATER_INDEX,
                             LOGS_FOLDER,
                             LOG_FILENAME,
                             False,
                             req_host) for mpis in mpiss if (
                                mpis.id %
                                PARALLELISM_NUM_WORKERS) +
                            1 == pw]).apply_async(
                        task_id=_tid))
                time.sleep(2)  # wait 2 seconds for each async launches
            mpiss_processing = worsica_api_models.MergedProcessImageSet.objects.filter(
                jobSubmission=job_submission, processState__in=[
                    'merged', 'stored-rgb', 'interpolated-products', 'processing']).order_by('-sensingDate')
            while mpiss_processing.count() > 0:
                print('[startProcessing MergedProcessImageSet]: There are still images to be processed, waiting children to finish...')
                time.sleep(10)
                mpiss_processing = worsica_api_models.MergedProcessImageSet.objects.filter(
                    jobSubmission=job_submission, processState__in=[
                        'merged', 'stored-rgb', 'interpolated-products', 'processing']).order_by('-sensingDate')
            mpiss = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['processed', 'error-storing-processing']).order_by('-sensingDate')
            print('[startProcessing MergedProcessImageSet]: Start storing processing...')
            task_identifier = get_task_identifier(job_submission)
            print(task_identifier)
            for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                print('[startProcessing MergedProcessImageSet]: Launch children ' + str(pw))
                _tid = task_identifier + '-storing-processing-child' + str(pw)
                _list_mpiss = [
                    (mpis.id,
                     SERVICE,
                     USER_ID,
                     ROI_ID,
                     SIMULATION_ID,
                     PREV_JOB_SUBMISSION_STATE,
                     WATER_INDEX,
                     False,
                     False,
                     req_host) for mpis in mpiss if (
                        mpis.id %
                        PARALLELISM_NUM_WORKERS) +
                    1 == pw]
                result.append(tasks.taskStartProcessingStore.starmap(_list_mpiss).apply_async(task_id=_tid))
                time.sleep(2)  # wait 2 seconds for each async launches
            mpiss_storing = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['processed', 'storing-processing']).order_by('-sensingDate')
            while mpiss_storing.count() > 0:  # or ampiss_storing.count()>0:
                print('[startProcessing MergedProcessImageSet]: There are still merged or climatology images to be stored, waiting children to finish...')
                print('[startProcessing MergedProcessImageSet]: debug mpiss_storing=' + str(mpiss_storing.count()))
                time.sleep(10)
                mpiss_storing = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['processed', 'storing-processing']).order_by('-sensingDate')
        except Exception as e:
            print(traceback.format_exc())
            if len(result) > 0:
                print('[startProcessing MergedProcessImageSet]: Revoking children to finish...')
                for r in result:
                    r.revoke(terminate=True, signal='SIGKILL')
        # TODO: on waterleak do not throw error on job submission for error-download-corrupt images
        # just continue
        if (SERVICE == 'waterleak'):
            _change_job_submission_state(job_submission, 'processed')
        else:
            _change_job_submission_state(job_submission, 'processed')

    # ---------------waterleak only---------------
    if (SERVICE == 'waterleak'):
        # ------------------climatology----------------
        if (job_submission.state in ['processed', 'error-generating-average']):
            PREV_JOB_SUBMISSION_STATE = job_submission.state
            _change_job_submission_state(job_submission, 'generating-average')
            ampiss = worsica_api_models.AverageMergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['submitted', 'error-generating-average']).order_by('-date')
            result = []
            try:
                print('[startProcessing AverageMergedProcessImageSet]: Start generating climatology...')
                task_identifier = get_task_identifier(job_submission)
                print(task_identifier)
                for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                    print('[startProcessing AverageMergedProcessImageSet]: Launch children ' + str(pw))
                    _tid = task_identifier + '-generating-average-img-child' + str(pw)
                    result.append(
                        tasks.taskStartGeneratingClimatologyProcess.starmap(
                            [
                                (ampis.id,
                                 SERVICE,
                                 USER_ID,
                                 ROI_ID,
                                 SIMULATION_ID,
                                 PREV_JOB_SUBMISSION_STATE,
                                 WATER_INDEX,
                                 LOGS_FOLDER,
                                 LOG_FILENAME,
                                 req_host) for ampis in ampiss if (
                                    ampis.id %
                                    PARALLELISM_NUM_WORKERS) +
                                1 == pw]).apply_async(
                            task_id=_tid))
                    time.sleep(2)  # wait 2 seconds for each async launches
                ampiss_generating = worsica_api_models.AverageMergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['submitted', 'generating-average']).order_by('-date')
                while ampiss_generating.count() > 0:
                    print('[startProcessing AverageMergedProcessImageSet]: There are still images to be processed, waiting children to finish...')
                    time.sleep(10)
                    ampiss_generating = worsica_api_models.AverageMergedProcessImageSet.objects.filter(
                        jobSubmission=job_submission, processState__in=['submitted', 'generating-average']).order_by('-date')
                ampiss = worsica_api_models.AverageMergedProcessImageSet.objects.filter(
                    jobSubmission=job_submission, processState__in=[
                        'generated-average', 'error-storing-generating-average']).order_by('-date')
                print('[startProcessing AverageMergedProcessImageSet]: Start storing climatology...')
                task_identifier = get_task_identifier(job_submission)
                print(task_identifier)
                for pw in range(1, PARALLELISM_NUM_WORKERS + 1):
                    print('[startProcessing AverageMergedProcessImageSet]: Launch children ' + str(pw))
                    _tid = task_identifier + '-storing-generating-average-child' + str(pw)
                    _list_ampiss = [
                        (ampis.id,
                         SERVICE,
                         USER_ID,
                         ROI_ID,
                         SIMULATION_ID,
                         PREV_JOB_SUBMISSION_STATE,
                         WATER_INDEX,
                         ('climatology_' in ampis.name),
                         False,
                         req_host) for ampis in ampiss if (
                            ampis.id %
                            PARALLELISM_NUM_WORKERS) +
                        1 == pw]
                    result.append(tasks.taskStartProcessingStore.starmap(_list_ampiss).apply_async(task_id=_tid))
                    time.sleep(2)  # wait 2 seconds for each async launches
                ampiss_storing = worsica_api_models.AverageMergedProcessImageSet.objects.filter(
                    jobSubmission=job_submission, processState__in=[
                        'generated-average', 'storing-generating-average']).order_by('-date')
                while ampiss_storing.count() > 0:  # or ampiss_storing.count()>0:
                    print('[startProcessing AverageMergedProcessImageSet]: There are still merged or climatology images to be stored, waiting children to finish...')
                    print('[startProcessing AverageMergedProcessImageSet]: debug ampiss_storing=' + str(ampiss_storing.count()))
                    time.sleep(10)
                    ampiss_storing = worsica_api_models.AverageMergedProcessImageSet.objects.filter(
                        jobSubmission=job_submission, processState__in=[
                            'generated-average', 'storing-generating-average']).order_by('-date')
            except Exception as e:
                print(traceback.format_exc())
                if len(result) > 0:
                    print('[startProcessing AverageMergedProcessImageSet]: Revoking children to finish...')
                    for r in result:
                        r.revoke(terminate=True, signal='SIGKILL')
            # TODO: on waterleak do not throw error on job submission for error-download-corrupt images
            # just continue
            if (SERVICE == 'waterleak'):
                _change_job_submission_state(job_submission, 'generated-average')
            else:
                _change_job_submission_state(job_submission, 'generated-average')

    # ---------------------------

    # 6- storing
    if (job_submission.state in ['processed', 'generated-average']):  # ,'error-storing']):

        # TODO: on waterleak do not throw error on job submission for error-download-corrupt images
        # just continue
        if (SERVICE == 'waterleak'):
            _change_job_submission_state(job_submission, 'stored')
        else:
            _change_job_submission_state(job_submission, 'stored')

    if (job_submission.state in ['stored']):
        PREV_JOB_SUBMISSION_STATE = job_submission.state
        job_submission.lastFinishedAt = datetime.datetime.now()
        job_submission.save()
        _change_job_submission_state(job_submission, 'success')

    # send email
    worsica_api_utils.notify_processing_state(job_submission)

    return HttpResponse(json.dumps({'simulation_id': job_submission.simulation_id, "job_submission_id": job_submission.id, 'state': job_submission.state}), content_type='application/json')


@csrf_exempt
def list_job_submission_dataverse(request, job_submission_id):
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        job_submission_dataverses = worsica_api_models.DataverseSubmission.objects.filter(jobSubmission=job_submission).order_by('-creationDate')
        job_submission_dataverse = []
        for jsd in job_submission_dataverses.filter(state__in=['dataverse-uploaded']):  # get the first one
            job_submission_dataverse.append({
                'id': jsd.id, 'name': jsd.name, 'doi': jsd.doi, 'state': jsd.state, 'creationDate': str(jsd.creationDate).split('.')[0],
                'url': '{0}/dataset.xhtml?persistentId={1}'.format(dataverse_access.DATAVERSE_BASE_URL, jsd.doi)})
            break
        # this state will look for the most recent created dataversesubmission and check the state
        last_submitted_dv_state = 'submitted'
        last_submitted_dv_time = None
        last_submitted_dv_name = None
        if (len(job_submission_dataverses) > 0):
            f = job_submission_dataverses.first()
            last_submitted_dv_state = f.state
            last_submitted_dv_time = str(f.creationDate).split('.')[0]
            last_submitted_dv_name = f.name
        j = {
            'simulation_id': job_submission.simulation_id,
            "job_submission_id": job_submission.id,
            'alert': 'success',
            'last_submitted_dv_state': last_submitted_dv_state,
            'last_submitted_dv_time': last_submitted_dv_time,
            'last_submitted_dv_name': last_submitted_dv_name,
            'datasets': job_submission_dataverse}

        return HttpResponse(json.dumps(j), content_type='application/json')
    except Exception as e:
        worsica_logger.info('[startProcessing storage]: error on list dataverse (' + str(e) + ')')
        j = {
            'simulation_id': job_submission.simulation_id,
            "job_submission_id": job_submission.id,
            'msg': {'msgTxt': 'Error on listing submissions!', 'msgColor': '#cc0000'},
            'alert': 'error'}
        return HttpResponse(json.dumps(j), content_type='application/json')


@csrf_exempt
def submit_job_submission_dataverse(request, job_submission_id):  # just only one
    _host = request.get_host()
    try:
        jsonReq = json.loads(request.body.decode('utf-8'))
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = roi_id)
        worsica_logger.info('[submit_job_submission_dataverse]: submit_job_submission_dataverse id=' + str(job_submission_id))
        tasks.taskSubmitProcessingDataverse.delay(job_submission.aos_id, job_submission.id, jsonReq, _host)
        return HttpResponse(json.dumps({'state': 'submitted', 'simulation_id': job_submission.simulation_id, 'id': job_submission.id}), content_type='application/json')
    except Exception as e:
        return HttpResponse(json.dumps({'state': 'error', 'simulation_id': job_submission.simulation_id, 'id': job_submission.id, 'error': str(e)}), content_type='application/json')


def submitProcessingDataverse(roi_id, job_submission_id, jsonReq, req_host):
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(aos_id=roi_id, id=job_submission_id, isVisible=True)
        # get the recent one (if exists)
        worsica_logger.info('[submitProcessingDataverse]: get the recent one if exists')
        recent_job_submission_dataverse = None
        job_submission_dataverses = worsica_api_models.DataverseSubmission.objects.filter(
            jobSubmission=job_submission).order_by('-creationDate')
        if len(job_submission_dataverses) > 0:
            recent_job_submission_dataverse = job_submission_dataverses.first()
            worsica_logger.info('[submitProcessingDataverse]: found least recent at ' + str(recent_job_submission_dataverse.creationDate))
        else:
            worsica_logger.info('[submitProcessingDataverse]: not found')
        # create new one
        worsica_logger.info('[submitProcessingDataverse]: create new one')
        # not quite sure
        job_submission_dataverse, _ = worsica_api_models.DataverseSubmission.objects.get_or_create(
            jobSubmission=job_submission, state__in=['dataverse-uploading', 'error-dataverse-uploading'])
        job_submission_dataverse.name = jsonReq['title']
        job_submission_dataverse.reference = 'dv' + str(job_submission_dataverse.id)
        job_submission_dataverse.state = 'dataverse-uploading'
        job_submission_dataverse.save()
        worsica_logger.info(job_submission_dataverse.state)
        worsica_logger.info('[submitProcessingDataverse]: storing processed products to the dataverse')
        piss = worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['resampled'])
        mpiss = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['success', 'stored'])
        ampiss = worsica_api_models.AverageMergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['success', 'stored'])
        gts = worsica_api_models.GenerateTopographyMap.objects.filter(jobSubmission=job_submission, processState__in=['success', 'stored-generated-topo'])
        dv_last_alias = dataverse_import.create_dataverse_user(
            dataverse_access.DATAVERSE_BASE_URL,
            dataverse_access.DATAVERSE_API_TOKEN,
            dataverse_access.DATAVERSE_ROOT_ALIAS,
            job_submission.service,
            job_submission.user_id,
            job_submission.aos_id,
            job_submission.simulation_id,
            jsonReq['owner'])
        ds = dataverse_import.generate_json_dataset(job_submission, piss, mpiss, ampiss, gts, jsonReq)
        worsica_logger.info(ds)
        try:
            jsonResponse = dataverse_import.dataverse_upload(dataverse_access.DATAVERSE_BASE_URL, dv_last_alias, dataverse_access.DATAVERSE_API_TOKEN, ds)
            j = {
                'simulation_id': job_submission.simulation_id,
                "job_submission_id": job_submission.id,
                'state': 'submitted',
            }
            job_submission_dataverse.state = 'dataverse-uploaded'
            job_submission_dataverse.doi = jsonResponse['pid']
            job_submission_dataverse.save()
            worsica_logger.info(job_submission_dataverse.state)
            # if everything goes well, delete older recent one (if exists)
            worsica_logger.info('[submitProcessingDataverse]: remove the least recent one')
            if recent_job_submission_dataverse is not None:
                # remove the dataset
                if recent_job_submission_dataverse.doi is not None:
                    try:
                        dataverse_import.delete_dataset(dataverse_access.DATAVERSE_BASE_URL, dataverse_access.DATAVERSE_API_TOKEN, recent_job_submission_dataverse.doi)
                    except Exception as e:
                        worsica_logger.info('[submitProcessingDataverse]: unable to remove, pass')
                        pass
                recent_job_submission_dataverse.delete()
                worsica_logger.info('[submitProcessingDataverse]: removed')
        except Exception as e:
            worsica_logger.info('[submitProcessingDataverse]: error on trying to store datafile to dataverse (' + str(e) + ')')
            j = {
                'simulation_id': job_submission.simulation_id,
                "job_submission_id": job_submission.id,
                'state': 'error'}
            job_submission_dataverse.state = 'error-dataverse-uploading'
            job_submission_dataverse.save()
            worsica_logger.info(job_submission_dataverse.state)
    except Exception as e:
        worsica_logger.info('[submitProcessingDataverse]: error on trying to store a dataverse (' + str(e) + ')')
        j = {'simulation_id': job_submission.simulation_id, "job_submission_id": job_submission.id, 'state': 'error'}
        job_submission_dataverse.state = 'error-dataverse-uploading'
        job_submission_dataverse.save()
        worsica_logger.info(job_submission_dataverse.state)

    worsica_api_utils.notify_dataverse_upload_state(job_submission, dataverse_access.DATAVERSE_BASE_URL)
    return HttpResponse(json.dumps(j), content_type='application/json')


@csrf_exempt
def show_job_submission_details(request, job_submission_id):
    try:
        jsonPis = []
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        obj = json.loads(job_submission.obj)
        piss = worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission).order_by('-small_name')
        for p in piss:
            jsonPis.append({
                'id': p.id,
                'name': p.name,
                'small_name': p.small_name,
                'state': p.processState
            })
        jsonMpis = []
        jsonAMpis = []
        mpiss = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission).order_by('-sensingDate')
        leastrecentdate = None
        mostrecentdate = None
        if len(mpiss) > 0:
            leastrecentdate = mpiss.last().sensingDate.strftime('%Y-%m-%d')
            mostrecentdate = mpiss.first().sensingDate.strftime('%Y-%m-%d')
        for mp in mpiss:
            jsonMpis.append({
                'id': mp.id,
                'name': mp.name,
                'small_name': mp.small_name,
                'state': mp.processState
            })
        jsonResponse = {
            "alert": "success",
            "job_submission_id": job_submission_id,
            "simulation_id": job_submission.simulation_id,
            "aos_id": job_submission.aos_id,
            "simulation_name": obj['areaOfStudy']['simulation']['name'],
            "service": job_submission.service,
            "provider": job_submission.provider,
            "state": job_submission.state,
            "lastSubmittedAt": str(job_submission.lastSubmittedAt),
            "lastRunAt": str(job_submission.lastRunAt),
            "lastFinishedAt": str(job_submission.lastFinishedAt),
            "processedImagesets": jsonPis,
            "mergedProcessedImagesets": jsonMpis,
            "leastrecentdate": leastrecentdate,
            "mostrecentdate": mostrecentdate,
        }
        if job_submission.service == 'waterleak':
            ampiss = worsica_api_models.AverageMergedProcessImageSet.objects.filter(jobSubmission=job_submission).order_by('-date')
            for amp in ampiss:
                jsonAMpis.append({
                    'id': amp.id,
                    'name': amp.name,
                    'small_name': amp.small_name,
                    'state': amp.processState
                })
            jsonResponse["averageMergedProcessedImagesets"] = jsonAMpis

        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(e)
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def show_job_submission(request, job_submission_id):
    try:
        showWhat = request.GET.get('showWhat')
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
        exec_args = json.loads(job_submission.exec_arguments)
        obj = json.loads(job_submission.obj)
        job_submission_exec = json.loads(job_submission.exec_arguments)
        WATER_INDEX_ARR = job_submission_exec['detection']['waterIndex'].split(',')
        if ('wiThreshold' in job_submission_exec['detection']):
            WI_THRESHOLD_ARR = job_submission_exec['detection']['wiThreshold'].split(',')
            wt = {}
            for w, t in zip(WATER_INDEX_ARR, WI_THRESHOLD_ARR):
                wt[w] = t
        jsonPis = []

        _topographies = worsica_api_models.GenerateTopographyMap.objects.filter(jobSubmission=job_submission)
        _clonedCoastlines = worsica_api_models.ShapelineMPIS.objects.filter(mprocessImageSet__jobSubmission=job_submission)
        _createdThresholds = worsica_api_models.ImageSetThreshold.objects.filter(processImageSet__jobSubmission=job_submission)

        jsonResponse = {
            "alert": "success",
            "job_submission_id": job_submission_id,
            "simulation_id": job_submission.simulation_id,
            "aos_id": job_submission.aos_id,
            "simulation_name": obj['areaOfStudy']['simulation']['name'],
            "service": job_submission.service,
            "provider": job_submission.provider,
            "state": job_submission.state,
            "numTopographies": {
                "submitted": _topographies.filter(processState__in=['submitted']).count(),
                "running": _topographies.filter(processState__in=['generating-topo', 'generated-topo', 'storing-generated-topo']).count(),
                "finished": _topographies.filter(processState__in=['success']).count(),
                "error": _topographies.filter(processState__in=['error-generating-topo', 'error-storing-generated-topo']).count(),
            },
            "numClonedCoastlines": {
                "submitted": _clonedCoastlines.filter(state__in=['submitted']).count(),
                "running": _clonedCoastlines.filter(state__in=['cloning']).count(),
                "finished": _clonedCoastlines.filter(state__in=['finished']).count(),
                "error": _clonedCoastlines.filter(state__in=['error-cloning']).count(),
            },
            "numCreatedThresholds": {
                "submitted": _createdThresholds.filter(state__in=['submitted']).count(),
                "running": _createdThresholds.filter(state__in=['calculating-threshold']).count(),
                "finished": _createdThresholds.filter(state__in=['finished']).count(),
                "error": _createdThresholds.filter(state__in=['error-calculating-threshold']).count(),
            },
            "roi_json": exec_args['roi'],
        }

        piss = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission).order_by('-sensingDate')
        HAS_MERGED_IMAGES = (len(piss) > 0)
        if HAS_MERGED_IMAGES:
            print('has MergedProcessImageSet')
        else:
            piss = worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission).order_by('-small_name')

        if (showWhat is None):
            for p in piss:
                job_outputs = {}
                # print(p.name)
                if HAS_MERGED_IMAGES:
                    shapeline_mpis = worsica_api_models.ShapelineMPIS.objects.filter(mprocessImageSet=p)  # , shapelineSourceType = 'processing')
                else:
                    shapeline_mpis = worsica_api_models.ShapelineMPIS.objects.filter(processImageSet=p)  # , shapelineSourceType = 'processing')

                thresholds_mpis = worsica_api_models.ImageSetThreshold.objects.filter(processImageSet=p)

                if HAS_MERGED_IMAGES:
                    rasters = worsica_api_models.MergedProcessImageSetRaster.objects.filter(processImageSet=p)
                else:
                    rasters = worsica_api_models.ProcessImageSetRaster.objects.filter(processImageSet=p)

                EXISTS_RUNNING_TMP_CLEANUP = False
                for shp in shapeline_mpis:
                    # check if there is a tmp running clone/cleanup
                    if ('_cleanup' in shp.name) and ('_tmp' in shp.name) and (shp.shapelineSourceType == 'cloning_and_cleaning') and (shp.state in ['cloning', 'submitted']):
                        EXISTS_RUNNING_TMP_CLEANUP = True
                EXISTS_RUNNING_TMP_THRESHOLD = False
                for thr in thresholds_mpis:
                    # check if there is a tmp running clone/cleanup
                    if ('_tmp' in thr.small_name) and (thr.state in ['calculating-threshold', 'submitted']):
                        EXISTS_RUNNING_TMP_THRESHOLD = True

                if rasters:
                    for r in rasters:
                        rl = (r.rasterLayer.id if r.rasterLayer is not None else None)
                        if r.indexType == 'RGB' and shapeline_mpis:
                            for shp in shapeline_mpis:
                                wi = shp.name.replace('_cleanup', '').replace('_tmp', '').replace('_threshold', '').split('_')[-1]
                                shp_mpis_id = shp.id
                                shplineSourceType = shp.shapelineSourceType
                                shpState = shp.state
                                if (wi not in job_outputs):
                                    job_outputs[wi] = []
                                _type = wi  # failsafe
                                _name = wi.upper()  # failsafe
                                if (job_submission.service == 'coastal'):
                                    _type = (wi + '_coastline' if wi in WATER_INDEX_ARR else 'coastline')
                                    _name = (wi.upper() + (' cleanup' if shplineSourceType == 'cloning_and_cleaning' else '') + ' Coastline' if wi in WATER_INDEX_ARR else 'Coastline')
                                elif (job_submission.service == 'inland'):
                                    _type = (wi + '_waterbody' if wi in WATER_INDEX_ARR else 'waterbody')  # stay as it is
                                    _name = (wi.upper() + (' cleanup' if shplineSourceType == 'cloning_and_cleaning' else '') + ' Waterbody' if wi in WATER_INDEX_ARR else 'Waterbody')
                                _name = ('[Manual Threshold] ' if shp.isFromManualThreshold else '') + _name  # add prefix
                                _name += (' (' + shp.small_name + ')' if shplineSourceType == 'cloning_and_cleaning' else '')
                                # hide_cleanup_product checks if there is a tmp cleanup running and the shp is a cleanup that has finished. if yes, hide it
                                EXISTS_FINISHED_CLEANUP = ('_cleanup' in shp.name) and ('_tmp' not in shp.name) and (shp.shapelineSourceType == 'cloning_and_cleaning') and (shp.state in ['finished'])
                                EXISTS_FINISHED_THRESHOLD = ('_threshold' in shp.name) and ('_tmp' not in shp.name) and (shp.shapelineSourceType == 'processing') and (shp.state in ['not-applicable'])
                                hide_cleanup_product = EXISTS_RUNNING_TMP_CLEANUP and EXISTS_FINISHED_CLEANUP
                                hide_threshold_product = EXISTS_RUNNING_TMP_THRESHOLD and EXISTS_FINISHED_THRESHOLD
                                if not hide_cleanup_product and not hide_threshold_product:
                                    job_outputs[wi].append({
                                        'group': wi,
                                        'type': _type,
                                        'name': _name,
                                        'roi_id': job_submission.aos_id,
                                        'simulation_id': job_submission.simulation_id,
                                        'intermediate_process_imageset_id': p.id,
                                        'shp_mpis_id': shp_mpis_id,
                                        'shplineSourceType': shplineSourceType,
                                        'clone_state': shpState,
                                        'thr_id': (shp.imageSetThreshold.id if (shp.imageSetThreshold and shp.isFromManualThreshold) else None),
                                        'rgb': {
                                            'type': r.indexType,
                                            'name': r.get_indexType_display(),
                                            'roi_id': job_submission.aos_id,
                                            'simulation_id': job_submission.simulation_id,
                                            'intermediate_process_imageset_id': p.id,
                                            'raster_id': rl
                                        }
                                    })
                        elif r.indexType == 'RGB':
                            wi = 'RGB'
                            job_outputs[wi] = []
                            job_outputs[wi].append({
                                'group': wi,
                                'type': 'RGB',
                                'name': 'RGB',
                                'roi_id': job_submission.aos_id,
                                'simulation_id': job_submission.simulation_id,
                                'intermediate_process_imageset_id': p.id,
                                'raster_id': rl,
                                'rgb': {
                                        'type': r.indexType,
                                        'name': r.get_indexType_display(),
                                        'roi_id': job_submission.aos_id,
                                        'simulation_id': job_submission.simulation_id,
                                        'intermediate_process_imageset_id': p.id,
                                        'raster_id': rl
                                }
                            })
                        else:
                            hide_cleanup_product = EXISTS_RUNNING_TMP_CLEANUP and '_cleanup' in r.indexType
                            if not hide_cleanup_product:
                                shp_clone = (' (' + r.clone_cleanup_small_name + ')' if r.clone_cleanup_small_name != '' else '')
                                threshold = None
                                isAutoThreshold = False
                                wi = r.indexType.split('_')[0]
                                if (wi not in job_outputs):
                                    job_outputs[wi] = []
                                if ('wiThreshold' in job_submission_exec['detection']) and r.indexType in WATER_INDEX_ARR:
                                    threshold = float(wt[r.indexType]) if wt[r.indexType] != 'AUTO' else float(r.autoThresholdValue)
                                    isAutoThreshold = (wt[r.indexType] == 'AUTO')
                                _type = r.indexType
                                _name = r.get_indexType_display() + shp_clone
                                _name = ('[Manual Threshold] ' if r.isFromManualThreshold else '') + _name
                                job_outputs[wi].append({
                                    'group': wi,
                                    'type': _type,
                                    'name': _name,
                                    'roi_id': job_submission.aos_id,
                                    'simulation_id': job_submission.simulation_id,
                                    'intermediate_process_imageset_id': p.id,
                                    'raster_id': rl,
                                    'threshold': threshold,
                                    'isAutoThreshold': isAutoThreshold,
                                    'isCustomThreshold': False,
                                })

                # thresholds (binary and ced)
                threshold_states = {wi: None for wi in WATER_INDEX_ARR}
                if (job_submission.service == 'coastal'):
                    if thresholds_mpis:
                        if not EXISTS_RUNNING_TMP_THRESHOLD:
                            thr = thresholds_mpis.order_by('-id')[0]  # get the most recent
                            if (thr.state == 'finished'):
                                rasters = worsica_api_models.ImageSetThresholdRaster.objects.filter(imageSetThreshold=thr)
                                if rasters:
                                    for r in rasters:
                                        rl = (r.rasterLayer.id if r.rasterLayer is not None else None)
                                        wi = r.indexType.split('_')[0]
                                        job_outputs[wi].append({
                                            'group': wi,
                                            'type': r.indexType.lower() + '_threshold',
                                            'name': '[Manual Threshold] ' + r.get_indexType_display(),
                                            'roi_id': job_submission.aos_id,
                                            'simulation_id': job_submission.simulation_id,
                                            'intermediate_process_imageset_id': p.id,
                                            'isCustomThreshold': True,
                                            'threshold': float(thr.threshold),  # if thr.threshold else None,
                                            'threshold_name': thr.name,  # if thr.threshold else None,
                                            'raster_id': rl,
                                            'thr_id': thr.id
                                        })
                        # get threshold states for each image
                        for thr in thresholds_mpis.order_by('-id'):
                            rasters = worsica_api_models.ImageSetThresholdRaster.objects.filter(imageSetThreshold=thr)
                            if rasters:
                                wi = rasters[0].indexType.split('_')[0]
                                threshold_states[wi] = thr.state

                for wi in job_outputs:
                    job_outputs[wi] = sorted(job_outputs[wi], key=itemgetter('name'))

                jsonPisResponse = {
                    'isTopography': False,
                    'processState': p.processState,
                    'threshold_states': threshold_states,
                    'name': p.name,
                    'small_name': p.small_name,
                    'id': p.id,
                    'sensingDate': (str(p.sensingDate) if (job_submission.service == 'waterleak') else None),
                    'job_outputs': job_outputs,
                }
                jsonPis.append(jsonPisResponse)
            jsonResponse["processedImagesets"] = jsonPis

            # topographies
            jsonTopo = []
            if (job_submission.service == 'coastal'):
                gtms = worsica_api_models.GenerateTopographyMap.objects.filter(jobSubmission=job_submission)
                for gtm in gtms:
                    job_outputs2 = {}
                    if (gtm.processState == 'success'):
                        rasters = worsica_api_models.GenerateTopographyMapRaster.objects.filter(generateTopographyMap=gtm)
                        if rasters:
                            for r in rasters:
                                rl = (r.rasterLayer.id if r.rasterLayer is not None else None)
                                wi = r.indexType.split('_')[0]
                                if (wi not in job_outputs2):
                                    job_outputs2[wi] = []
                                if r.topoKind == 'topo':
                                    job_outputs2[wi].append({
                                        'group': r.indexType.split('_')[0].lower(),
                                        'type': r.indexType.lower() + '_topography',
                                        'name': r.get_indexType_display() + ' Topography',
                                        'roi_id': job_submission.aos_id,
                                        'simulation_id': job_submission.simulation_id,
                                        'gt_id': gtm.id,
                                        'raster_id': rl
                                    })
                                elif r.topoKind == 'flood-freq':
                                    job_outputs2[wi].append({
                                        'group': r.indexType.split('_')[0].lower(),
                                        'type': r.indexType.lower() + '_floodfreq',
                                        'name': r.get_indexType_display() + ' Flood Frequency',
                                        'roi_id': job_submission.aos_id,
                                        'simulation_id': job_submission.simulation_id,
                                        'gt_id': gtm.id,
                                        'raster_id': rl
                                    })
                    jsonTopo.append({
                        'isTopography': True,
                        'processState': gtm.processState,
                        'name': '_generate_topo_' + gtm.name,
                        'small_name': 'Topography ' + gtm.small_name + ' (' + gtm.get_generateTopographyFrom_display().replace('From ', '') + ')',
                        'id': gtm.id,
                        'job_outputs': job_outputs2,
                    })
                jsonResponse['coastalTopographies'] = jsonTopo

            # climatologies
            jsonAMpis = []
            if (job_submission.service == 'waterleak'):
                ampiss = worsica_api_models.AverageMergedProcessImageSet.objects.filter(jobSubmission=job_submission).order_by('-date')
                for amp in ampiss:
                    job_outputs2 = {}
                    if (amp.processState == 'success'):
                        rasters = worsica_api_models.AverageMergedProcessImageSetRaster.objects.filter(amprocessImageSet=amp)
                        if rasters:
                            for r in rasters:
                                rl = (r.rasterLayer.id if r.rasterLayer is not None else None)
                                wi = r.indexType.split('_')[0]
                                if (wi not in job_outputs2):
                                    job_outputs2[wi] = []
                                job_outputs2[wi].append({
                                    'group': r.indexType.split('_')[0],
                                    'type': r.indexType,
                                    'name': r.get_indexType_display(),
                                    'roi_id': job_submission.aos_id,
                                    'simulation_id': job_submission.simulation_id,
                                    'intermediate_process_imageset_id': amp.id,
                                    'raster_id': rl,
                                    'is_climatology': True,
                                })
                    jsonAMpis.append({
                        'processState': amp.processState,
                        'name': amp.name,
                        'small_name': amp.small_name,
                        'id': amp.id,
                        'job_outputs': job_outputs2,
                    })
                jsonResponse['averageMergedProcessedImagesets'] = jsonAMpis

        elif (showWhat == 'showLeakDetections'):
            # leak detections
            jsonLeakDetections = []
            if (job_submission.service == 'waterleak'):
                if HAS_MERGED_IMAGES:  # merged only
                    lds = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.filter(jobSubmission=job_submission)
                    for ld in lds:
                        jsonPisLeakRasterResponse = []
                        rlds = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.filter(processImageSet=ld).order_by('id')
                        for rld in rlds:
                            jsonPisLeakRasterResponse.append({
                                'group': ld.indexType.split('_')[0],
                                'type': ld.indexType,
                                'start_processing_second_deriv_from': ld.start_processing_second_deriv_from,
                                'ldType': rld.ldType,
                                'name': ld.get_indexType_display() + ' ' + rld.get_ldType_display(),
                                'roi_id': job_submission.aos_id,
                                'simulation_id': job_submission.simulation_id,
                                'intermediate_process_imageset_id': (ld.processImageSet.id if ld.processImageSet else None),
                                'intermediate_ucprocess_imageset_id': (ld.ucprocessImageSet.id if ld.ucprocessImageSet else None),
                                'intermediate_leak_id': ld.id,
                                'raster_id': (rld.rasterLayer.id if rld.rasterLayer is not None else None),
                            })

                        us_rlds = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSaved.objects.filter(leak_detection=ld).order_by('id')
                        for us_rld in us_rlds:
                            us_rlds_lp = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPoints.objects.filter(user_leak_points=us_rld).order_by('id')
                            if len(us_rlds_lp) > 0:  # has points
                                jsonPisLeakRasterResponse.append({
                                    'group': ld.indexType.split('_')[0],
                                    'type': ld.indexType,
                                    'start_processing_second_deriv_from': ld.start_processing_second_deriv_from,
                                    'lp_user_saved': True,
                                    'lp_user_saved_id': us_rld.id,
                                    'ldType': 'uslp' + str(us_rld.id) + '_leak_points',  # us_rld.ldType
                                    'name': ld.get_indexType_display() + ' Leak Points (' + us_rld.name + ')',
                                    'roi_id': job_submission.aos_id,
                                    'simulation_id': job_submission.simulation_id,
                                    'intermediate_process_imageset_id': (ld.processImageSet.id if ld.processImageSet else None),
                                    'intermediate_ucprocess_imageset_id': (ld.ucprocessImageSet.id if ld.ucprocessImageSet else None),
                                    'intermediate_leak_id': ld.id,
                                    'raster_id': None,  # (us_rld.rasterLayer.id if us_rld.rasterLayer is not None else None),
                                })
                        ld_masks = worsica_api_models.MergedProcessImageSetForLeakDetectionMask.objects.filter(processImageSet=ld).order_by('id')
                        if len(ld_masks) > 0:  # has mask/pipe network
                            ld_mask = ld_masks[0]  # get the only one
                            if ld_mask.generated_from_ids is not None:
                                for geom_id in ld_mask.generated_from_ids.split(','):
                                    try:
                                        geom = worsica_api_models.UserRepositoryGeometry.objects.get(id=geom_id)
                                        jsonPisLeakRasterResponse.append({
                                            'group': ld.indexType.split('_')[0],
                                            'type': ld.indexType,
                                            'start_processing_second_deriv_from': ld.start_processing_second_deriv_from,
                                            'is_geometry': True,
                                            'geometry_id': int(geom.id),
                                            'ldType': 'ld' + str(ld.id) + '_geom' + str(geom.id) + '_pipe_network',  # us_rld.ldType
                                            'name': 'Pipe Network ' + geom.name,
                                            'roi_id': job_submission.aos_id,
                                            'simulation_id': job_submission.simulation_id,
                                            'intermediate_process_imageset_id': (ld.processImageSet.id if ld.processImageSet else None),
                                            'intermediate_ucprocess_imageset_id': (ld.ucprocessImageSet.id if ld.ucprocessImageSet else None),
                                            'intermediate_leak_id': ld.id,
                                            'raster_id': None,  # (us_rld.rasterLayer.id if us_rld.rasterLayer is not None else None),
                                        })
                                    except Exception as e:
                                        print('Skip. Unable to find geometry with id=' + geom_id)
                                        pass

                        jsonLeakDetections.append({
                            'ld_id': ld.id,
                            'name': ld.name,
                            'type': ld.indexType,
                            'processState': ld.processState,
                            'start_processing_second_deriv_from': ld.start_processing_second_deriv_from,
                            'imagename': (ld.processImageSet.small_name if ld.processImageSet else (ld.ucprocessImageSet.small_name.replace('User chosen merged', 'ESA') if ld.ucprocessImageSet else None)),
                            'outputs': jsonPisLeakRasterResponse,
                        })
            jsonResponse["leakDetections"] = jsonLeakDetections

        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(e)
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "response": str(e),
            'traceback': str(traceback.format_exc())
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def list_job_submission_imagesets(request, job_submission_id):
    try:
        print(job_submission_id)
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
        obj = json.loads(job_submission.obj)
        exec_args = json.loads(job_submission.exec_arguments)
        kwargsMPIS = {}
        kwargsMPIS['jobSubmission'] = job_submission
        if (request.GET.get('begin_date_ld')):
            kwargsMPIS['sensingDate__gte'] = request.GET.get('begin_date_ld')
        if (request.GET.get('end_date_ld')):
            kwargsMPIS['sensingDate__lte'] = request.GET.get('end_date_ld')
        smpiss = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission).order_by('-sensingDate')
        mpiss = smpiss.filter(**kwargsMPIS).order_by('-sensingDate')
        jsonPis = []
        for p in mpiss:
            uuids = []
            if p.procImageSet_ids != '':
                piss = worsica_api_models.ProcessImageSet.objects.filter(id__in=p.procImageSet_ids.split(','))
                uuids = [i.imageSet.uuid for i in piss]
                # print(uuids)
            jsonPis.append({'processState': p.processState, 'name': p.name, 'small_name': p.small_name, 'id': p.id, 'sensingDate': str(p.sensingDate), 'uuids': uuids})
        leastrecentdate = None
        mostrecentdate = None
        if len(smpiss) > 0:
            leastrecentdate = smpiss.last().sensingDate.strftime('%Y-%m-%d')
            mostrecentdate = smpiss.first().sensingDate.strftime('%Y-%m-%d')
        jsonResponse = {
            "alert": "success",
            "job_submission_id": job_submission_id,
            "simulation_id": job_submission.simulation_id,
            "aos_id": job_submission.aos_id,
            "simulation_name": obj['areaOfStudy']['simulation']['name'],
            "service": job_submission.service,
            "provider": job_submission.provider,
            "state": job_submission.state,
            "roi_json": exec_args['roi'],
            "processedImagesets": jsonPis,
            "leastrecentdate": str(leastrecentdate),
            "mostrecentdate": str(mostrecentdate),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(e)
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def show_job_submission_raster_leakdetection(request, job_submission_id, leak_detection_id, process_imageset_id, determine_leak, raster_type, leak_detection_out_type, raster_id):
    # determine_leak: by_index or by_anomaly
    # leak_detection_output_type: anomaly, 2nd_deriv or anomaly_2nd_deriv
    try:
        print(job_submission_id, process_imageset_id, determine_leak, raster_type, leak_detection_out_type, raster_id)
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
        try:
            pis = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(
                id=leak_detection_id,
                jobSubmission=job_submission,
                indexType=raster_type,
                processImageSet__id=process_imageset_id,
                start_processing_second_deriv_from=determine_leak)
            print(pis)
            print('has MergedProcessImageSetForLeakDetection')
            print('has rasters associated to MergedProcessImageSetForLeakDetection')
            raster = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.get(processImageSet=pis, ldType=leak_detection_out_type, rasterLayer__id=raster_id)
        except ObjectDoesNotExist:
            print('no MergedProcessImageSetForLeakDetection')
        rtbmeta = raster_models.RasterLayerBandMetadata.objects.get(rasterlayer_id=raster.rasterLayer.id, band=0)
        rtbmeta_min = rtbmeta.min
        rtbmeta_max = rtbmeta.max
        jsonResponse = {
            "alert": 'success',
            "job_submission_id": job_submission_id,
            "process_imageset_id": process_imageset_id,
            "raster_type": raster_type,
            "leak_detection_out_type": leak_detection_out_type,
            "raster_id": raster_id
        }
        if (leak_detection_out_type == '2nd_deriv'):
            rgb_rtbmeta_min = [44, 219, 255]
            rgb_rtbmeta_middle = [80, 80, 80]
            rgb_rtbmeta_max = [10, 10, 10]
        else:
            rgb_rtbmeta_min = [0, 0, 0]
            rgb_rtbmeta_middle = [128, 128, 128]
            rgb_rtbmeta_max = [255, 255, 255]
        jsonResponse["response"] = {
            'url': '/raster/tiles/' + str(raster_id),
            'rgbcolorfrom': rgb_rtbmeta_min, 'min': rtbmeta_min,
            'rgbcolorto': rgb_rtbmeta_max, 'max': rtbmeta_max,
            'rgbcolorover': rgb_rtbmeta_middle
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(e)
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def show_job_submission_raster_leakdetection_uc(request, job_submission_id, leak_detection_id, ucprocess_imageset_id, determine_leak, raster_type, leak_detection_out_type, raster_id):
    # determine_leak: by_index or by_anomaly
    # leak_detection_output_type: anomaly, 2nd_deriv or anomaly_2nd_deriv
    try:
        print(job_submission_id, ucprocess_imageset_id, determine_leak, raster_type, leak_detection_out_type, raster_id)
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
        try:
            pis = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(
                id=leak_detection_id,
                jobSubmission=job_submission,
                indexType=raster_type,
                ucprocessImageSet__id=ucprocess_imageset_id,
                start_processing_second_deriv_from=determine_leak)
            print(pis)
            print('has MergedProcessImageSetForLeakDetection')
            print('has rasters associated to MergedProcessImageSetForLeakDetection')
            raster = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.get(processImageSet=pis, ldType=leak_detection_out_type, rasterLayer__id=raster_id)
        except ObjectDoesNotExist:
            print('no MergedProcessImageSetForLeakDetection')
        rtbmeta = raster_models.RasterLayerBandMetadata.objects.get(rasterlayer_id=raster.rasterLayer.id, band=0)
        rtbmeta_min = rtbmeta.min
        rtbmeta_max = rtbmeta.max
        jsonResponse = {
            "alert": 'success',
            "job_submission_id": job_submission_id,
            "process_imageset_id": ucprocess_imageset_id,
            "raster_type": raster_type,
            "leak_detection_out_type": leak_detection_out_type,
            "raster_id": raster_id
        }
        if (leak_detection_out_type == '2nd_deriv'):
            rgb_rtbmeta_min = [44, 219, 255]
            rgb_rtbmeta_middle = [80, 80, 80]
            rgb_rtbmeta_max = [10, 10, 10]
        else:
            rgb_rtbmeta_min = [0, 0, 0]
            rgb_rtbmeta_middle = [128, 128, 128]
            rgb_rtbmeta_max = [255, 255, 255]
        jsonResponse["response"] = {
            'url': '/raster/tiles/' + str(raster_id),
            'rgbcolorfrom': rgb_rtbmeta_min, 'min': rtbmeta_min,
            'rgbcolorto': rgb_rtbmeta_max, 'max': rtbmeta_max,
            'rgbcolorover': rgb_rtbmeta_middle
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(e)
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def show_job_submission_raster_climatology(request, job_submission_id, process_imageset_id, raster_type, raster_id):
    try:
        print(job_submission_id, process_imageset_id, raster_type, raster_id)
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
        try:
            pis = worsica_api_models.AverageMergedProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
            print('has AverageMergedProcessImageSet')
            print('has rasters associated to AverageMergedProcessImageSet')
            raster = worsica_api_models.AverageMergedProcessImageSetRaster.objects.get(amprocessImageSet=pis, indexType=raster_type, rasterLayer__id=raster_id)
        except ObjectDoesNotExist:
            print('no AverageMergedProcessImageSet')
        rtbmeta = raster_models.RasterLayerBandMetadata.objects.get(rasterlayer_id=raster.rasterLayer.id, band=0)
        rtbmeta_min = rtbmeta.min
        rtbmeta_max = rtbmeta.max
        jsonResponse = {
            "alert": 'success',
            "job_submission_id": job_submission_id,
            "process_imageset_id": process_imageset_id,
            "raster_type": raster_type,
            "raster_id": raster_id
        }
        rgb_rtbmeta_min = [0, 0, 0]
        rgb_rtbmeta_middle = [128, 128, 128]
        rgb_rtbmeta_max = [255, 255, 255]
        jsonResponse["response"] = {
            'url': '/raster/tiles/' + str(raster_id),
            'rgbcolorfrom': rgb_rtbmeta_min, 'min': rtbmeta_min,
            'rgbcolorto': rgb_rtbmeta_max, 'max': rtbmeta_max,
            'rgbcolorover': rgb_rtbmeta_middle
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(e)
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def show_job_submission_raster(request, job_submission_id, process_imageset_id, raster_type, raster_id):
    try:
        print(job_submission_id, process_imageset_id, raster_type, raster_id)
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
        try:
            pis = worsica_api_models.MergedProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
            if '_threshold' in raster_type:
                print('has ImageSetThresholdRaster')
                print('has rasters associated to ImageSetThresholdRaster')
                iSThr = worsica_api_models.ImageSetThreshold.objects.get(processImageSet=pis)
                raster = worsica_api_models.ImageSetThresholdRaster.objects.get(imageSetThreshold=iSThr, indexType=raster_type.replace('_threshold', ''), rasterLayer__id=raster_id)
            else:
                print('has MergedProcessImageSet')
                print('has rasters associated to MergedProcessImageSet')
                raster = worsica_api_models.MergedProcessImageSetRaster.objects.get(processImageSet=pis, indexType=raster_type, rasterLayer__id=raster_id)
        except ObjectDoesNotExist:
            print('has ProcessImageSet')
            pis = worsica_api_models.ProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
            print('has rasters associated to ProcessImageSet')
            raster = worsica_api_models.ProcessImageSetRaster.objects.get(processImageSet=pis, indexType=raster_type, rasterLayer__id=raster_id)
        rtbmeta = raster_models.RasterLayerBandMetadata.objects.get(rasterlayer_id=raster.rasterLayer.id, band=0)
        rtbmeta_min = rtbmeta.min
        rtbmeta_max = rtbmeta.max

        jsonResponse = {
            "alert": 'success',
            "job_submission_id": job_submission_id,
            "process_imageset_id": process_imageset_id,
            "raster_type": raster_type,
            "raster_id": raster_id
        }
        if raster_type == 'RGB':
            jsonResponse["response"] = {
                'url': '/raster/algebra',
                'min': rtbmeta_min,
                'max': rtbmeta_max
            }
        else:
            rgb_rtbmeta_min = [0, 0, 0]
            rgb_rtbmeta_middle = [128, 128, 128]
            rgb_rtbmeta_max = [255, 255, 255]
            jsonResponse["response"] = {
                'url': '/raster/tiles/' + str(raster_id),
                'rgbcolorfrom': rgb_rtbmeta_min, 'min': rtbmeta_min,
                'rgbcolorto': rgb_rtbmeta_max, 'max': rtbmeta_max,
                'rgbcolorover': rgb_rtbmeta_middle
            }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(e)
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def show_job_submission_raster_histogram(request, job_submission_id, process_imageset_id, raster_type, raster_id):
    try:
        print(job_submission_id, process_imageset_id, raster_type, raster_id)
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
        try:
            pis = worsica_api_models.MergedProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
            print('has MergedProcessImageSet')
            print('has rasters associated to MergedProcessImageSet')
            raster = worsica_api_models.MergedProcessImageSetRaster.objects.get(processImageSet=pis, indexType=raster_type, rasterLayer__id=raster_id)
        except ObjectDoesNotExist:
            print('has ProcessImageSet')
            pis = worsica_api_models.ProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
            print('has rasters associated to ProcessImageSet')
            raster = worsica_api_models.ProcessImageSetRaster.objects.get(processImageSet=pis, indexType=raster_type, rasterLayer__id=raster_id)
        rtbmeta = raster_models.RasterLayerBandMetadata.objects.get(rasterlayer_id=raster.rasterLayer.id, band=0)
        rtbmetaCategories = [round(b, 2) for b in rtbmeta.hist_bins]
        rtbmetaValues = rtbmeta.hist_values

        jsonResponse = {
            "alert": 'success',
            "job_submission_id": job_submission_id,
            "process_imageset_id": process_imageset_id,
            "raster_type": raster_type,
            "raster_id": raster_id
        }

        jsonResponse["response"] = {
            'rtbmetaCategories': rtbmetaCategories,
            'rtbmetaValues': rtbmetaValues
        }

        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(e)
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def show_job_submission_topography_raster(request, job_submission_id, process_gt_id, raster_type, raster_id):
    try:
        print(job_submission_id, process_gt_id, raster_type, raster_id)
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
        print(raster_type)
        raster_type = raster_type.replace('_topography', '').replace('_floodfreq', '').upper()
        print(raster_type)
        gtm = worsica_api_models.GenerateTopographyMap.objects.get(jobSubmission=job_submission, id=process_gt_id)
        print(gtm)
        raster = worsica_api_models.GenerateTopographyMapRaster.objects.get(generateTopographyMap=gtm, indexType=raster_type, rasterLayer__id=raster_id)

        rtbmeta = raster_models.RasterLayerBandMetadata.objects.get(rasterlayer_id=raster.rasterLayer.id, band=0)
        rtbmeta_min = rtbmeta.min
        rtbmeta_max = rtbmeta.max

        customColorspace = True

        jsonResponse = {
            "alert": 'success',
            "job_submission_id": job_submission_id,
            "process_gt_id": process_gt_id,
            "raster_type": raster_type,
            "raster_id": raster_id,
            "customColorspace": customColorspace
        }
        if customColorspace:
            minlevel = rtbmeta_min - 0.01
            maxlevel = rtbmeta_max + 0.01
            MAX_COLORS = 24  # 16
            csLegends = []
            cmap = plt.cm.get_cmap('jet', MAX_COLORS)    # PiYG
            vals = np.linspace(minlevel, maxlevel, cmap.N)
            for i in range(cmap.N):
                rgba = cmap(i)
                # rgb2hex accepts rgb or rgba
                hexColor = matplotlib.colors.rgb2hex(rgba)
                csLegends.append([f'x>={vals[i]:2.2f}', f'{hexColor[1:]}'])

            colormap = {c[0]: c[1] for c in csLegends}
            jsonResponse["response"] = {
                'url': '/raster/algebra',
                'colormap': str(colormap)
            }
        else:
            rgb_rtbmeta_min = [0, 0, 0]
            rgb_rtbmeta_middle = [128, 128, 128]
            rgb_rtbmeta_max = [255, 255, 255]
            jsonResponse["response"] = {
                'url': '/raster/tiles/' + str(raster_id),
                'rgbcolorfrom': rgb_rtbmeta_min, 'min': rtbmeta_min,
                'rgbcolorto': rgb_rtbmeta_max, 'max': rtbmeta_max,
                'rgbcolorover': rgb_rtbmeta_middle
            }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(e)
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def download_job_submission_products_climatology(request, job_submission_id, process_imageset_id):
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)

        SERVICE = job_submission.service
        USER_ID = str(job_submission.user_id)
        ROI_ID = str(job_submission.aos_id)
        SIMULATION_ID = str(job_submission.simulation_id)
        WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
        PATH_TO_PRODUCTS = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/' + WORKSPACE_SIMULATION_FOLDER
        NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER

        pis = worsica_api_models.AverageMergedProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
        print('has AverageMergedProcessImageSet')

        IMAGESET_NAME = pis.name
        zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '-final-products.zip'
        appropriateFileName = IMAGESET_NAME + '-final-products.zip'
        return HttpResponse(json.dumps({'state': 'ok', 'url': str(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + zipFileName), 'appropriateFileName': appropriateFileName,
                                        'user': nextcloud_access.NEXTCLOUD_USER, 'pwd': nextcloud_access.NEXTCLOUD_PWD}), content_type='application/json')
    except Exception as e:
        print(e)
        return HttpResponse(json.dumps({'state': 'error', 'error': 'worsica-intermediate:' + str(e)}), content_type='application/json')


@csrf_exempt
def download_job_submission_products(request, job_submission_id, process_imageset_id):
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)

        SERVICE = job_submission.service
        USER_ID = str(job_submission.user_id)
        ROI_ID = str(job_submission.aos_id)
        SIMULATION_ID = str(job_submission.simulation_id)
        WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
        PATH_TO_PRODUCTS = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/' + WORKSPACE_SIMULATION_FOLDER
        NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER
        try:
            pis = worsica_api_models.MergedProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
            print('has MergedProcessImageSet')
        except ObjectDoesNotExist:
            print('has ProcessImageSet')
            pis = worsica_api_models.ProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
        IMAGESET_NAME = pis.name
        zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '-final-products.zip'
        appropriateFileName = IMAGESET_NAME + '-final-products.zip'
        return HttpResponse(json.dumps({'state': 'ok', 'url': str(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + zipFileName), 'appropriateFileName': appropriateFileName,
                                        'user': nextcloud_access.NEXTCLOUD_USER, 'pwd': nextcloud_access.NEXTCLOUD_PWD}), content_type='application/json')
    except Exception as e:
        print(e)
        return HttpResponse(json.dumps({'state': 'error', 'error': 'worsica-intermediate:' + str(e)}), content_type='application/json')


@csrf_exempt
def download_job_submission_leakdetection_leakpoints(request, job_submission_id, leak_detection_id, process_imageset_id, determine_leak, raster_type, leak_detection_out_type, raster_id):
    # determine_leak: by_index or by_anomaly
    # leak_detection_output_type: anomaly, 2nd_deriv or anomaly_2nd_deriv
    job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
    ld = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(
        id=leak_detection_id,
        jobSubmission=job_submission,
        indexType=raster_type,
        processImageSet__id=process_imageset_id,
        start_processing_second_deriv_from=determine_leak)
    rld = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.get(processImageSet=ld, rasterLayer__id=raster_id, ldType=leak_detection_out_type)

    filename = job_submission.name + '-' + ld.processImageSet.name + '-' + raster_type + '-' + leak_detection_out_type + '-' + determine_leak + '.kml'
    kml = "<?xml version='1.0' encoding='UTF-8'?>\n"
    kml += "<kml xmlns='http://www.opengis.net/kml/2.2'>\n"
    kml += '<Document>\n'
    kml += "<name>" + filename + "</name>\n"

    if rld.ldType in ['2nd_deriv', 'anomaly_2nd_deriv']:
        rlds_lp = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPoints.objects.filter(second_deriv_raster=rld, showLeakPoint=True).order_by('id')
        for dlp in rlds_lp:
            kml += "<Placemark>\n"
            kml += "<name>Leak point " + str(dlp.id) + "</name>\n"
            kml += "<ExtendedData><Data name='pixelValue'><value>" + str(dlp.pixelValue) + "</value></Data></ExtendedData>\n"
            point = OGRGeometry('SRID=3857;POINT(' + str(dlp.xCoordinate) + ' ' + str(dlp.yCoordinate) + ')')
            point.transform(4326)
            kml += "<Point><coordinates>" + str(point.x) + ',' + str(point.y) + ",0</coordinates></Point>\n"
            kml += "</Placemark>\n"
    kml += '</Document>\n'
    kml += "</kml>"
    return HttpResponse(json.dumps({'state': 'ok', 'filename': filename, 'kml': kml}), content_type='application/json')


@csrf_exempt
def download_job_submission_leakdetection_leakpoints_uc(request, job_submission_id, leak_detection_id, ucprocess_imageset_id, determine_leak, raster_type, leak_detection_out_type, raster_id):
    # determine_leak: by_index or by_anomaly
    # leak_detection_output_type: anomaly, 2nd_deriv or anomaly_2nd_deriv
    job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
    ld = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(
        id=leak_detection_id,
        jobSubmission=job_submission,
        indexType=raster_type,
        ucprocessImageSet__id=ucprocess_imageset_id,
        start_processing_second_deriv_from=determine_leak)
    rld = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.get(processImageSet=ld, rasterLayer__id=raster_id, ldType=leak_detection_out_type)

    filename = job_submission.name + '-' + ld.processImageSet.name + '-' + raster_type + '-' + leak_detection_out_type + '-' + determine_leak + '.kml'
    kml = "<?xml version='1.0' encoding='UTF-8'?>\n"
    kml += "<kml xmlns='http://www.opengis.net/kml/2.2'>\n"
    kml += '<Document>\n'
    kml += "<name>" + filename + "</name>\n"

    if rld.ldType in ['2nd_deriv', 'anomaly_2nd_deriv']:
        rlds_lp = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPoints.objects.filter(second_deriv_raster=rld, showLeakPoint=True).order_by('id')
        for dlp in rlds_lp:
            kml += "<Placemark>\n"
            kml += "<name>Leak point " + str(dlp.id) + "</name>\n"
            kml += "<ExtendedData><Data name='pixelValue'><value>" + str(dlp.pixelValue) + "</value></Data></ExtendedData>\n"
            point = OGRGeometry('SRID=3857;POINT(' + str(dlp.xCoordinate) + ' ' + str(dlp.yCoordinate) + ')')
            point.transform(4326)
            kml += "<Point><coordinates>" + str(point.x) + ',' + str(point.y) + ",0</coordinates></Point>\n"
            kml += "</Placemark>\n"
    kml += '</Document>\n'
    kml += "</kml>"
    return HttpResponse(json.dumps({'state': 'ok', 'filename': filename, 'kml': kml}), content_type='application/json')


@csrf_exempt
def change_job_submission_leakdetection_leakpoint(request, job_submission_id, leak_detection_id, process_imageset_id, determine_leak, raster_type, leak_detection_out_type, raster_id, leak_point_id):
    # determine_leak: by_index or by_anomaly
    # leak_detection_output_type: anomaly, 2nd_deriv or anomaly_2nd_deriv
    jsonResponse = {}
    try:
        jsonLeakPoint = {}
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
        ld = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(
            id=leak_detection_id,
            jobSubmission=job_submission,
            processImageSet__id=process_imageset_id,
            start_processing_second_deriv_from=determine_leak)
        rld = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.get(processImageSet=ld, rasterLayer__id=raster_id, indexType=raster_type, ldType=leak_detection_out_type)
        if rld.ldType in ['2nd_deriv', 'anomaly_2nd_deriv']:
            rld_lp = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPoints.objects.get(id=leak_point_id, second_deriv_raster=rld)
            rld_lp.showLeakPoint = (not rld_lp.showLeakPoint)
            rld_lp.save()
            jsonLeakPoint = {'id': rld_lp.id, 'xCoordinate': rld_lp.xCoordinate, 'yCoordinate': rld_lp.yCoordinate, 'pixelValue': rld_lp.pixelValue, 'showLeakPoint': rld_lp.showLeakPoint}
        jsonResponse = {
            "alert": 'success',
            "job_submission_id": job_submission_id,
            "process_imageset_id": process_imageset_id,
            "determine_leak": determine_leak,
            "raster_type": raster_type,
            "leak_detection_out_type": leak_detection_out_type,
            "raster_id": raster_id,
            "leak_point": jsonLeakPoint
        }
    except Exception as e:
        print(e)
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "process_imageset_id": process_imageset_id,
            "determine_leak": determine_leak,
            "raster_type": raster_type,
            "leak_detection_out_type": leak_detection_out_type,
            "raster_id": raster_id,
            "response": str(e),
        }
    return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def change_job_submission_leakdetection_leakpoint_uc(request, job_submission_id, leak_detection_id, ucprocess_imageset_id,
                                                     determine_leak, raster_type, leak_detection_out_type, raster_id, leak_point_id):
    # determine_leak: by_index or by_anomaly
    # leak_detection_output_type: anomaly, 2nd_deriv or anomaly_2nd_deriv
    jsonResponse = {}
    try:
        jsonLeakPoint = {}
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
        ld = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(
            id=leak_detection_id,
            jobSubmission=job_submission,
            indexType=raster_type,
            ucprocessImageSet__id=ucprocess_imageset_id,
            start_processing_second_deriv_from=determine_leak)
        rld = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.get(processImageSet=ld, rasterLayer__id=raster_id, ldType=leak_detection_out_type)
        if rld.ldType in ['2nd_deriv', 'anomaly_2nd_deriv']:
            rld_lp = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPoints.objects.get(id=leak_point_id, second_deriv_raster=rld)
            rld_lp.showLeakPoint = (not rld_lp.showLeakPoint)
            rld_lp.save()
            jsonLeakPoint = {'id': rld_lp.id, 'xCoordinate': rld_lp.xCoordinate, 'yCoordinate': rld_lp.yCoordinate, 'pixelValue': rld_lp.pixelValue, 'showLeakPoint': rld_lp.showLeakPoint}
        jsonResponse = {
            "alert": 'success',
            "job_submission_id": job_submission_id,
            "process_imageset_id": ucprocess_imageset_id,
            "determine_leak": determine_leak,
            "raster_type": raster_type,
            "leak_detection_out_type": leak_detection_out_type,
            "raster_id": raster_id,
            "leak_point": jsonLeakPoint
        }
    except Exception as e:
        print(e)
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "process_imageset_id": ucprocess_imageset_id,
            "determine_leak": determine_leak,
            "raster_type": raster_type,
            "leak_detection_out_type": leak_detection_out_type,
            "raster_id": raster_id,
            "response": str(e),
        }
    return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def show_job_submission_leakdetection_leakpoints(request, job_submission_id, leak_detection_id, process_imageset_id, determine_leak, raster_type, leak_detection_out_type, raster_id):
    # determine_leak: by_index or by_anomaly
    # leak_detection_output_type: anomaly, 2nd_deriv or anomaly_2nd_deriv
    jsonResponse = {}
    show_max_leak_points = 10000  # max points
    show_only_visible = True  # show only visible points or all of them?
    if request.GET.get('edit_mode'):
        show_max_leak_points = 10000
        show_only_visible = False
    elif request.GET.get('max_leak_points'):
        show_max_leak_points = int(request.GET.get('max_leak_points'))
        show_only_visible = True

    print(show_max_leak_points)
    try:
        print(job_submission_id, leak_detection_id, process_imageset_id, determine_leak, raster_type, leak_detection_out_type, raster_id)
        jsonLeakPoints = []
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
        ld = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(
            id=leak_detection_id,
            jobSubmission=job_submission,
            indexType=raster_type,
            processImageSet__id=process_imageset_id,
            start_processing_second_deriv_from=determine_leak)
        rld = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.get(processImageSet=ld, rasterLayer__id=raster_id, ldType=leak_detection_out_type)
        if rld.ldType in ['2nd_deriv', 'anomaly_2nd_deriv']:
            if show_only_visible:
                rlds_lp = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPoints.objects.filter(second_deriv_raster=rld, showLeakPoint=True).order_by('id')
            else:
                rlds_lp = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPoints.objects.filter(second_deriv_raster=rld).order_by('id')
            jsonLeakPoints = [{'id': rld_lp.id, 'xCoordinate': rld_lp.xCoordinate, 'yCoordinate': rld_lp.yCoordinate,
                               'pixelValue': rld_lp.pixelValue, 'showLeakPoint': rld_lp.showLeakPoint} for rld_lp in rlds_lp[:show_max_leak_points]]
        jsonResponse = {
            "alert": 'success',
            "job_submission_id": job_submission_id,
            "process_imageset_id": process_imageset_id,
            "determine_leak": determine_leak,
            "raster_type": raster_type,
            "leak_detection_out_type": leak_detection_out_type,
            "raster_id": raster_id,
            "leak_points": jsonLeakPoints,
        }

    except Exception as e:
        print(e)
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "process_imageset_id": process_imageset_id,
            "determine_leak": determine_leak,
            "raster_type": raster_type,
            "leak_detection_out_type": leak_detection_out_type,
            "raster_id": raster_id,
            "response": str(e),
        }
    return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


def show_job_submission_leakdetection_leakpoints_uc(request, job_submission_id, leak_detection_id, ucprocess_imageset_id, determine_leak, raster_type, leak_detection_out_type, raster_id):
    # determine_leak: by_index or by_anomaly
    # leak_detection_output_type: anomaly, 2nd_deriv or anomaly_2nd_deriv
    jsonResponse = {}
    show_max_leak_points = 10000  # max points
    show_only_visible = True  # show only visible points or all of them?
    if request.GET.get('edit_mode'):
        show_max_leak_points = 10000
        show_only_visible = False
    elif request.GET.get('max_leak_points'):
        show_max_leak_points = int(request.GET.get('max_leak_points'))
        show_only_visible = True

    print(show_max_leak_points)
    try:
        print(job_submission_id, leak_detection_id, ucprocess_imageset_id, determine_leak, raster_type, leak_detection_out_type, raster_id)
        jsonLeakPoints = []
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
        ld = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(
            id=leak_detection_id,
            jobSubmission=job_submission,
            indexType=raster_type,
            ucprocessImageSet__id=ucprocess_imageset_id,
            start_processing_second_deriv_from=determine_leak)
        rld = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.get(processImageSet=ld, rasterLayer__id=raster_id, ldType=leak_detection_out_type)
        if rld.ldType in ['2nd_deriv', 'anomaly_2nd_deriv']:
            if show_only_visible:
                rlds_lp = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPoints.objects.filter(second_deriv_raster=rld, showLeakPoint=True).order_by('id')
            else:
                rlds_lp = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPoints.objects.filter(second_deriv_raster=rld).order_by('id')
            jsonLeakPoints = [{'id': rld_lp.id, 'xCoordinate': rld_lp.xCoordinate, 'yCoordinate': rld_lp.yCoordinate,
                               'pixelValue': rld_lp.pixelValue, 'showLeakPoint': rld_lp.showLeakPoint} for rld_lp in rlds_lp[:show_max_leak_points]]
        jsonResponse = {
            "alert": 'success',
            "job_submission_id": job_submission_id,
            "process_imageset_id": ucprocess_imageset_id,
            "determine_leak": determine_leak,
            "raster_type": raster_type,
            "leak_detection_out_type": leak_detection_out_type,
            "raster_id": raster_id,
            "leak_points": jsonLeakPoints,
        }

    except Exception as e:
        print(e)
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "process_imageset_id": ucprocess_imageset_id,
            "determine_leak": determine_leak,
            "raster_type": raster_type,
            "leak_detection_out_type": leak_detection_out_type,
            "raster_id": raster_id,
            "response": str(e),
        }
    return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def show_job_submission_shapeline(request, job_submission_id, process_imageset_id, shp_mpis_id):
    try:
        features = []
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
        try:
            pis = worsica_api_models.MergedProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
            print('has MergedProcessImageSet')
            shplines = worsica_api_models.Shapeline.objects.filter(shapelineMPIS__id=shp_mpis_id, shapelineMPIS__mprocessImageSet=pis)
        except ObjectDoesNotExist:
            print('has ProcessImageSet')
            pis = worsica_api_models.ProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
            shplines = worsica_api_models.Shapeline.objects.filter(shapelineMPIS__id=shp_mpis_id, shapelineMPIS__processImageSet=pis)
        for shpline in shplines:
            shp_mls = shpline.mls
            feature = json.loads(shp_mls.json)
            # gdal 3.0.4 must have changed the way shp are generated
            # instead of lonlat, they are generated as latlon, causing issues on loading them to map
            # this is a workaround to fix that by passing latlon to lonlat
            _featcoordinates = [[[f[1], f[0]] for f in feat]
                                for feat in feature['coordinates']]
            feature['coordinates'] = _featcoordinates
            features.append({'type': 'Feature', 'geometry': feature})
        fc = {
            'type': 'FeatureCollection',
            'crs': {
                    'type': 'name',
                    'properties': {
                            'name': 'EPSG:4326'
                    }
            },
            'features': features
        }
        exec_args = json.loads(job_submission.exec_arguments)
        obj = json.loads(job_submission.obj)
        jsonResponse = {
            "alert": "success",
            "job_submission_id": job_submission_id,
            "simulation_id": job_submission.id,
            "aos_id": job_submission.aos_id,
            "pis_id": pis.id,
            "json": fc,
            "simulation_name": obj['areaOfStudy']['simulation']['name'],
            "service": job_submission.service,
            "provider": job_submission.provider,
            "state": job_submission.state,
            "roi_json": exec_args['roi']}
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def clone_cleanup_all_job_submission_shapelines(request, job_submission_id):
    _host = request.get_host()
    try:
        jsonReq = json.loads(request.body.decode('utf-8'))
        print(jsonReq)
        slugify_cn = slugify(jsonReq['custom_name'])
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        piss = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission)
        job_outputs2 = []
        for pis in piss:
            r = worsica_api_models.MergedProcessImageSetRaster.objects.get(processImageSet=pis, indexType='RGB')
            actual_shapeline_groups = worsica_api_models.ShapelineMPIS.objects.filter(mprocessImageSet=pis, shapelineSourceType='processing')
            for actual_shapeline_group in actual_shapeline_groups:
                cloned_shapeline_group, _ = worsica_api_models.ShapelineMPIS.objects.get_or_create(mprocessImageSet=actual_shapeline_group.mprocessImageSet, name=actual_shapeline_group.name.replace(
                    '_cleanup', '') + '_cleanup_tmp', reference=actual_shapeline_group.name.replace('_cleanup', '') + '_cleanup_tmp', small_name=slugify_cn, shapelineSourceType='cloning_and_cleaning')
                cloned_shapeline_group.state = 'submitting'
                cloned_shapeline_group.save()

                rm_shapeline_group_id = None
                try:
                    rm_shapeline_group = worsica_api_models.ShapelineMPIS.objects.get(
                        mprocessImageSet=actual_shapeline_group.mprocessImageSet, name=actual_shapeline_group.name.replace(
                            '_cleanup', '') + '_cleanup', shapelineSourceType='cloning_and_cleaning')
                    rm_shapeline_group_id = rm_shapeline_group.id
                except Exception as e3:
                    print('No old cleanup shp, skip')
                    pass

                task_identifier = get_task_identifier_cloned_shapeline_group(job_submission, cloned_shapeline_group.id)
                print(task_identifier)
                process_imageset_id = pis.id
                tasks.taskStartCloneCleanup.apply_async(
                    args=[
                        job_submission_id,
                        process_imageset_id,
                        actual_shapeline_group.id,
                        cloned_shapeline_group.id,
                        True,
                        jsonReq,
                        _host],
                    task_id=task_identifier)

                shp = cloned_shapeline_group
                wi = shp.name.replace('_cleanup_tmp', '').split('_')[-1]
                shp_mpis_id = shp.id
                shplineSourceType = shp.shapelineSourceType
                shpState = shp.state

                _type = wi  # failsafe
                _name = wi.upper()  # failsafe
                if (job_submission.service == 'coastal'):
                    _type = (wi + '_coastline')
                    _name = (wi.upper() + ' Coastline')
                elif (job_submission.service == 'inland'):
                    _type = (wi + '_waterbody')  # stay as it is
                    _name = (wi.upper() + ' Waterbody')
                _name += (' (' + shp.small_name + ')' if shplineSourceType == 'cloning_and_cleaning' else '')
                rl = (r.rasterLayer.id if r.rasterLayer is not None else None)
                job_outputs2.append({
                    'group': wi,
                    'type': _type,
                    'name': _name,
                    'roi_id': job_submission.aos_id,
                    'simulation_id': job_submission.simulation_id,
                    'intermediate_process_imageset_id': pis.id,
                    'shp_mpis_id': shp_mpis_id,
                    'shplineSourceType': shplineSourceType,
                    'clone_state': shpState,
                    'rm_shapeline_group_id': rm_shapeline_group_id,
                    'add_shapeline_group_id': cloned_shapeline_group.id,
                    'rgb': {
                        'type': r.indexType,
                        'name': r.get_indexType_display(),
                        'roi_id': job_submission.aos_id,
                        'simulation_id': job_submission.simulation_id,
                        'intermediate_process_imageset_id': pis.id,
                        'raster_id': rl
                    }
                })

        jsonResponse = {
            "alert": 'success',
            "job_submission_id": job_submission_id,
            'shapeline': job_outputs2
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def create_threshold_job_submission(request, job_submission_id, process_imageset_id, raster_type):
    _host = request.get_host()
    try:
        jsonReq = json.loads(request.body.decode('utf-8'))
        slugify_cn = slugify(jsonReq['custom_name'])
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        pis = worsica_api_models.MergedProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)

        rm_created_threshold_id = None
        try:
            rm_created_threshold = worsica_api_models.ImageSetThreshold.objects.get(processImageSet=pis)  # , reference=slugify_cn)
            rm_created_threshold_id = rm_created_threshold.id
            if rm_created_threshold.state == 'error-calculating-threshold':  # delete failed
                rm_created_threshold.delete()
        except Exception as e3:
            print('No created threshold, skip')
            pass

        created_threshold, _ = worsica_api_models.ImageSetThreshold.objects.get_or_create(
            processImageSet=pis, name=jsonReq['custom_name'], reference=slugify_cn + '_tmp', small_name=slugify_cn + '_tmp')
        created_threshold.state = 'submitting'
        created_threshold.threshold = jsonReq['threshold_value']
        created_threshold.save()

        task_identifier = get_task_identifier_created_threshold(job_submission, created_threshold.id)
        print(task_identifier)
        tasks.taskStartCreateThreshold.apply_async(args=[job_submission_id, process_imageset_id, raster_type, created_threshold.id, rm_created_threshold_id, jsonReq, _host], task_id=task_identifier)

        job_outputs = []
        ct = created_threshold
        wi = raster_type
        _type = wi  # failsafe
        _name = wi.upper()  # failsafe
        _name += ' [' + ct.name + ']'

        job_outputs.append({
            'group': wi,
            'type': _type,
            'name': _name,
            'roi_id': job_submission.aos_id,
            'simulation_id': job_submission.simulation_id,
            'intermediate_process_imageset_id': pis.id,
            'threshold': float(ct.threshold) if ct.threshold else None,
            'threshold_name': ct.name,
            'isCustomThreshold': True
        })

        jsonResponse = {
            "alert": 'success',
            "job_submission_id": job_submission_id,
            'rm_created_threshold_id': rm_created_threshold_id,
            'threshold_states': {wi: ct.state},
            'image': job_outputs
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')

    except Exception as e:
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


def startCreateThreshold(job_submission_id, process_imageset_id, raster_type, created_threshold_id, rm_created_threshold_id, jsonReq, host):
    tmpPathZipFileName = None  # path for input zip
    myfilenamezip = None  # path for output zip
    try:
        sys.path.insert(1, '/usr/local/worsica_web_products')
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        pis = worsica_api_models.MergedProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
        created_threshold = worsica_api_models.ImageSetThreshold.objects.get(id=created_threshold_id)
        created_threshold.state = 'calculating-threshold'
        created_threshold.save()

        SERVICE = job_submission.service
        USER_ID = job_submission.user_id
        ROI_ID = job_submission.aos_id
        SIMULATION_ID = job_submission.simulation_id
        slugify_cn = slugify(jsonReq['custom_name'])
        threshold = float(jsonReq['threshold_value'])
        WORKSPACE_SIMULATION_FOLDER = 'threshold' + str(created_threshold_id) + '/' + SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
        PATH_TO_PRODUCTS = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/' + WORKSPACE_SIMULATION_FOLDER
        NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)

        IMAGESET_NAME = pis.name  # mpis
        INDEX_TYPE = raster_type  # IMAGESET_NAME_INDEX_TYPE.split('_')[-1] #merged_resampled_xxxxxx_yyyy_aweish
        IMAGESET_NAME_INDEX_TYPE = IMAGESET_NAME + '_' + INDEX_TYPE

        # 1- download original zip
        print('1- download original zip')
        zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '-final-products'
        zipFileName2 = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '_' + INDEX_TYPE + '_threshold-final-products'
        output_path = PATH_TO_PRODUCTS + '/' + IMAGESET_NAME
        output_path2 = WORKSPACE_SIMULATION_FOLDER + '/' + IMAGESET_NAME
        r2 = requests.get(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + zipFileName + '.zip', auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
        worsica_logger.info('[startProcessing storage]: download ' + zipFileName + '.zip')
        tmpPathZipFileName = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/' + zipFileName + '.zip'
        if r2.status_code == 200:
            with open(tmpPathZipFileName, 'wb') as f:
                f.write(r2.content)
        worsica_logger.info('[startProcessing storage]: unzip ' + zipFileName + '.zip')
        zipF = zipfile.ZipFile(tmpPathZipFileName, 'r')
        zipF.extractall(settings.WORSICA_FOLDER_PATH + '/worsica_web_products/threshold' + str(created_threshold_id) + '/')

        # 2- worsicaApplyWaterIndexesThreshold(img, threshold, actual_path):
        print('2- worsicaApplyWaterIndexesThreshold')
        import worsica_waterIndexes
        img = output_path + '/' + IMAGESET_NAME_INDEX_TYPE + '.tif'
        fout = output_path + '/' + IMAGESET_NAME_INDEX_TYPE + "_threshold_binary.tif"  # _WATERINDEX.tif -> _WATERINDEX_binary.tif
        worsica_waterIndexes.worsicaApplyWaterIndexesThreshold(img, threshold, output_path, fout)

        # 3- run_cleanup_edge_detection
        print('3- run_cleanup_edge_detection')
        import worsica_ph5_edge_detection
        fout1 = output_path + '/' + IMAGESET_NAME_INDEX_TYPE + "_threshold_binary_closing_edge_detection.tif"
        worsica_ph5_edge_detection.run_cleanup_edge_detection(fout, fout1)

        # 4- run_multiline_string_vectorization(image, threshold, elev, output_name)
        print('4- run_multiline_string_vectorization')
        import worsica_ph6_vectorization_v3
        shp_output = IMAGESET_NAME_INDEX_TYPE + '_threshold'
        fout2 = output_path + '/' + shp_output + '.shp'
        worsica_ph6_vectorization_v3.run_multiline_string_vectorization(img, threshold, 0, fout2)

        # 5- Store and replace binary image
        SHAPELINE_SOURCE_TYPE = 'generate_threshold'
        print('5- Store and replace binary image')
        itypes = [[INDEX_TYPE + '_threshold_binary', INDEX_TYPE + '_bin'], [INDEX_TYPE + '_threshold_binary_closing_edge_detection', INDEX_TYPE + '_ced']]
        for itype in itypes:
            subtasks._store_raster(created_threshold, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, IMAGESET_NAME, False, False, itype, PATH_TO_PRODUCTS,
                                   False, False, False, SHAPELINE_SOURCE_TYPE, jsonReq['custom_name'])  # CLONE_AND_CLEAN_FROM=SHAPELINE_SOURCE_TYPE
        # 6- Store new shapeline
        print('6- Store new shapeline')
        subtasks._store_shapeline(created_threshold.processImageSet, IMAGESET_NAME, INDEX_TYPE + '_threshold', PATH_TO_PRODUCTS, SHAPELINE_SOURCE_TYPE, jsonReq['custom_name'], created_threshold_id)

        # 7- Store a zip
        print('7- Generate zip file')
        file_names = [shp_output + '_binary.tif', shp_output + '_binary_closing_edge_detection.tif', shp_output + '.dbf', shp_output + '.prj', shp_output + '.shx', shp_output + '.shp']
        myfilenamezip = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/' + zipFileName2 + '-threshold' + str(created_threshold_id) + '.zip'
        print(myfilenamezip)
        WSF_CUSTOM = WORKSPACE_SIMULATION_FOLDER.replace('threshold' + str(created_threshold_id) + '/', '')  # remove cloneid
        with zipfile.ZipFile(myfilenamezip, mode='w') as zf:
            for file_name in file_names:
                print(output_path + '/' + file_name)
                zf.write(output_path + '/' + file_name, WSF_CUSTOM + '/' + IMAGESET_NAME + '/' + file_name, compress_type=zipfile.ZIP_DEFLATED)
        print('Start upload file directly to nextcloud')
        auth = (nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD)
        with open(myfilenamezip, 'rb') as f:
            retry, max_retries = 1, 5
            isUploadSuccess = False
            while retry <= max_retries and not isUploadSuccess:
                print('...attempt ' + str(retry))
                req = requests.put(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + zipFileName2 + '.zip', data=f, auth=auth)
                if (req.status_code == 201 or req.status_code == 204):
                    print('Upload successful')
                    isUploadSuccess = True
                else:
                    print('Upload error ' + str(req.status_code))
                    time.sleep(5)
                    retry += 1
            if (retry > max_retries):
                raise Exception('Upload failed')

        print('8- Remove old threshold/rasters and rename the temporary ones')
        print('Remove old threshold')
        try:
            rm_created_threshold = worsica_api_models.ImageSetThreshold.objects.get(processImageSet=pis, id=rm_created_threshold_id)
            rm_created_threshold.delete()  # delete old threshold
        except Exception as e3:
            print('No created threshold, skip')
            pass
        print('Rename tmp shapeline')
        tmp_shp = worsica_api_models.ShapelineMPIS.objects.get(imageSetThreshold=created_threshold)
        tmp_shp.name = tmp_shp.name.replace('_tmp', '')
        tmp_shp.save()
        print('Rename tmp threshold')
        created_threshold.name = created_threshold.name.replace('_tmp', '')
        created_threshold.small_name = created_threshold.small_name.replace('_tmp', '')
        created_threshold.state = 'finished'
        created_threshold.save()
        print('OK')
        itypes = [[INDEX_TYPE + '_binary', INDEX_TYPE + '_bin'], [INDEX_TYPE + '_binary_closing_edge_detection', INDEX_TYPE + '_ced']]
        for itype in itypes:
            print(itype[0])
            try:
                print('Remove binary and closing edge cleanup rasters')
                raster = worsica_api_models.ImageSetThresholdRaster.objects.get(
                    name=SERVICE + '-u' + str(USER_ID) + '-r' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '-' + itype[1] + '-threshold',
                    imageSetThreshold=created_threshold,
                    indexType=itype[0])
                raster.rasterLayer.delete()
                raster.delete()
            except Exception as e2:
                print('No binary and closing edge cleanup rasters')
                pass
            print('-----')
            try:
                print('Rename tmp binary and closing edge cleanup rasters')
                raster = worsica_api_models.ImageSetThresholdRaster.objects.get(
                    name=SERVICE + '-u' + str(USER_ID) + '-r' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '-' + itype[1] + '-threshold-tmp',
                    imageSetThreshold=created_threshold,
                    indexType=itype[0])
                raster.name = raster.name.replace('-tmp', '')
                raster.save()
            except Exception as e2:
                print('No tmp binary and closing edge cleanup rasters')
                pass

        # remove zip files
        shutil.rmtree(settings.WORSICA_FOLDER_PATH + '/worsica_web_products/threshold' + str(created_threshold_id) + '/')
        if (tmpPathZipFileName and os.path.exists(tmpPathZipFileName)):
            print('Remove zip input file')
            os.remove(tmpPathZipFileName)
        if (myfilenamezip and os.path.exists(myfilenamezip)):
            print('Remove zip output file')
            os.remove(myfilenamezip)

    except Exception as e:
        print(traceback.format_exc())
        print('Remove tmp threshold')
        try:
            created_threshold.state = 'error-calculating-threshold'
            created_threshold.save()
        except Exception as e2:
            print('Not found, skip')
            pass
        print('Remove tmp binary and closing edge rasters')
        itypes = [[INDEX_TYPE + '_threshold_binary', INDEX_TYPE + '_bin'], [INDEX_TYPE + '_threshold_binary_closing_edge_detection', INDEX_TYPE + '_ced']]
        for itype in itypes:
            try:
                print(itype[0])
                rasters = worsica_api_models.ImageSetThresholdRaster.objects.filter(
                    name=SERVICE + '-u' + str(USER_ID) + '-r' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '-' + itype[1] + '-threshold-tmp',
                    imageSetThreshold=created_threshold,
                    indexType=itype[0])
                for raster in rasters:
                    raster.rasterLayer.delete()
                    raster.delete()
            except Exception as e2:
                print('Not found, skip')
                pass
        shutil.rmtree(settings.WORSICA_FOLDER_PATH + '/worsica_web_products/threshold' + str(created_threshold_id) + '/')
        if (tmpPathZipFileName and os.path.exists(tmpPathZipFileName)):
            print('Remove zip input file')
            os.remove(tmpPathZipFileName)
        if (myfilenamezip and os.path.exists(myfilenamezip)):
            print('Remove zip output file')
            os.remove(myfilenamezip)


@csrf_exempt
def clone_cleanup_job_submission_shapeline(request, job_submission_id, process_imageset_id, shp_mpis_id):
    _host = request.get_host()
    try:
        jsonReq = json.loads(request.body.decode('utf-8'))
        print(jsonReq)
        slugify_cn = slugify(jsonReq['custom_name'])
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        actual_shapeline_group = worsica_api_models.ShapelineMPIS.objects.get(id=shp_mpis_id)  # , mprocessImageSet = pis)
        pis = worsica_api_models.MergedProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
        r = worsica_api_models.MergedProcessImageSetRaster.objects.get(processImageSet=pis, indexType='RGB')
        name_ref = actual_shapeline_group.name.replace('_cleanup', '') + '_cleanup_tmp'
        cloned_shapeline_group, _ = worsica_api_models.ShapelineMPIS.objects.get_or_create(
            mprocessImageSet=actual_shapeline_group.mprocessImageSet, name=name_ref, reference=name_ref, small_name=slugify_cn, shapelineSourceType='cloning_and_cleaning')
        cloned_shapeline_group.state = 'submitting'
        cloned_shapeline_group.isFromManualThreshold = actual_shapeline_group.isFromManualThreshold
        cloned_shapeline_group.save()

        rm_shapeline_group_id = None
        try:
            rm_shapeline_group = worsica_api_models.ShapelineMPIS.objects.get(
                mprocessImageSet=actual_shapeline_group.mprocessImageSet, name=actual_shapeline_group.name.replace(
                    '_cleanup', '') + '_cleanup', shapelineSourceType='cloning_and_cleaning')
            rm_shapeline_group_id = rm_shapeline_group.id
        except Exception as e3:
            print('No old cleanup shp, skip')
            pass

        task_identifier = get_task_identifier_cloned_shapeline_group(job_submission, cloned_shapeline_group.id)
        print(task_identifier)
        # remove_outside_polygon=false to remove inside polygon
        tasks.taskStartCloneCleanup.apply_async(args=[job_submission_id, process_imageset_id, actual_shapeline_group.id, cloned_shapeline_group.id, False, jsonReq, _host], task_id=task_identifier)

        job_outputs = []
        shp = cloned_shapeline_group
        wi = shp.name.replace('_cleanup', '').replace('_threshold', '').replace('_tmp', '').split('_')[-1]
        shp_mpis_id = shp.id
        shplineSourceType = shp.shapelineSourceType
        shpState = shp.state
        _type = wi  # failsafe
        _name = wi.upper()  # failsafe
        if (job_submission.service == 'coastal'):
            _type = (wi + '_coastline')
            _name = (wi.upper() + ' Coastline')
        elif (job_submission.service == 'inland'):
            _type = (wi + '_waterbody')  # stay as it is
            _name = (wi.upper() + ' Waterbody')
        _name += (' (' + shp.small_name + ')' if shplineSourceType == 'cloning_and_cleaning' else '')
        rl = (r.rasterLayer.id if r.rasterLayer is not None else None)
        job_outputs.append({
            'group': wi,
            'type': _type,
            'name': _name,
            'roi_id': job_submission.aos_id,
            'simulation_id': job_submission.simulation_id,
            'intermediate_process_imageset_id': pis.id,
            'shp_mpis_id': shp_mpis_id,
            'shplineSourceType': shplineSourceType,
            'clone_state': shpState,
            'rm_shapeline_group_id': rm_shapeline_group_id,
            'add_shapeline_group_id': cloned_shapeline_group.id,
            'rgb': {
                'type': r.indexType,
                'name': r.get_indexType_display(),
                'roi_id': job_submission.aos_id,
                'simulation_id': job_submission.simulation_id,
                'intermediate_process_imageset_id': pis.id,
                'raster_id': rl
            }
        })

        jsonResponse = {
            "alert": 'success',
            "job_submission_id": job_submission_id,
            'shapeline': job_outputs
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


def startCloneCleanupProcessing(job_submission_id, process_imageset_id, actual_shapeline_group_id, cloned_shapeline_group_id, remove_outside_polygon, jsonReq, req_host):
    tmpPathZipFileName = None  # path for input zip
    myfilenamezip = None  # path for output zip
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        actual_shapeline_group = worsica_api_models.ShapelineMPIS.objects.get(id=actual_shapeline_group_id)
        pis = worsica_api_models.MergedProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
        cloned_shapeline_group = worsica_api_models.ShapelineMPIS.objects.get(id=cloned_shapeline_group_id)
        cloned_shapeline_group.state = 'cloning'
        cloned_shapeline_group.save()

        # generate shp
        SERVICE = job_submission.service
        USER_ID = job_submission.user_id
        ROI_ID = job_submission.aos_id
        SIMULATION_ID = job_submission.simulation_id
        slugify_cn = slugify(jsonReq['custom_name'])
        WORKSPACE_SIMULATION_FOLDER = 'clone' + str(cloned_shapeline_group_id) + '/' + SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
        PATH_TO_PRODUCTS = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/' + WORKSPACE_SIMULATION_FOLDER
        NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)

        IMAGESET_NAME = pis.name  # mpis
        IMAGESET_NAME_INDEX_TYPE = actual_shapeline_group.name.replace('_cleanup', '').replace('_threshold', '').replace('_tmp', '')  # remove any prefixes
        INDEX_TYPE = IMAGESET_NAME_INDEX_TYPE.split('_')[-1]  # (get "aweish")
        # merged_resampled_xxxxxx_yyyy_aweish_threshold (readd prefix for further processings)
        IMAGESET_NAME_INDEX_TYPE = IMAGESET_NAME_INDEX_TYPE + ('_threshold' if (actual_shapeline_group.isFromManualThreshold) else '')

        SHAPELINE_SOURCE_TYPE = actual_shapeline_group.shapelineSourceType
        print(SHAPELINE_SOURCE_TYPE)

        INDEX_THRESHOLD = None  # is the threshold value or AUTO if automatic
        job_submission_exec = json.loads(job_submission.exec_arguments)
        WATER_INDEX_ARR = job_submission_exec['detection']['waterIndex'].split(',')
        if ('wiThreshold' in job_submission_exec['detection']):
            WI_THRESHOLD_ARR = job_submission_exec['detection']['wiThreshold'].split(',')
            for w, t in zip(WATER_INDEX_ARR, WI_THRESHOLD_ARR):
                if w == INDEX_TYPE:
                    INDEX_THRESHOLD = t
        print('Threshold for ' + INDEX_TYPE + ' is ' + str(INDEX_THRESHOLD))

        if not os.path.exists(PATH_TO_PRODUCTS):
            os.makedirs(PATH_TO_PRODUCTS, exist_ok=True)
        print('Create outputs at: ' + PATH_TO_PRODUCTS)

        # 1- Get binary closeup edge image
        print('1- Get binary closeup edge image')
        if (actual_shapeline_group.isFromManualThreshold):
            print('manual threshold')
            if SHAPELINE_SOURCE_TYPE == 'cloning_and_cleaning':
                zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '_' + INDEX_TYPE + '_threshold_cleanup-final-products'
                zipFileName2 = zipFileName
                output_path = PATH_TO_PRODUCTS + '/' + IMAGESET_NAME + '_' + INDEX_TYPE + '_cleanup'
                output_path2 = WORKSPACE_SIMULATION_FOLDER + '/' + IMAGESET_NAME + '_' + INDEX_TYPE + '_cleanup'
            else:
                zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '_' + INDEX_TYPE + '_threshold-final-products'
                zipFileName2 = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '_' + INDEX_TYPE + '_threshold_cleanup-final-products'
                output_path = PATH_TO_PRODUCTS + '/' + IMAGESET_NAME
                output_path2 = WORKSPACE_SIMULATION_FOLDER + '/' + IMAGESET_NAME + '_' + INDEX_TYPE + '_cleanup'
        else:
            if SHAPELINE_SOURCE_TYPE == 'cloning_and_cleaning':
                zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '_' + INDEX_TYPE + '_cleanup-final-products'
                zipFileName2 = zipFileName
                output_path = PATH_TO_PRODUCTS + '/' + IMAGESET_NAME + '_' + INDEX_TYPE + '_cleanup'
                output_path2 = WORKSPACE_SIMULATION_FOLDER + '/' + IMAGESET_NAME + '_' + INDEX_TYPE + '_cleanup'
            else:
                zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '-final-products'
                zipFileName2 = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '_' + INDEX_TYPE + '_cleanup-final-products'
                output_path = PATH_TO_PRODUCTS + '/' + IMAGESET_NAME
                output_path2 = WORKSPACE_SIMULATION_FOLDER + '/' + IMAGESET_NAME
        tmpPathZipFileName = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/' + zipFileName + '-clone' + str(cloned_shapeline_group_id) + '.zip'
        r2 = requests.get(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + zipFileName + '.zip', auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
        worsica_logger.info('[startProcessing storage]: download ' + zipFileName + '.zip')
        if r2.status_code == 200:
            with open(tmpPathZipFileName, 'wb') as f:
                f.write(r2.content)
        worsica_logger.info('[startProcessing storage]: unzip ' + zipFileName + '.zip')
        zipF = zipfile.ZipFile(tmpPathZipFileName, 'r')
        zipF.extractall(settings.WORSICA_FOLDER_PATH + '/worsica_web_products/clone' + str(cloned_shapeline_group_id) + '/')
        print(output_path)
        print(tmpPathZipFileName)

        # 2- Remove (blackout) region on that image, and save
        print('2- Remove (blackout) region on that image, and save')
        polygons_for_removal = jsonReq['polygons']
        for f in polygons_for_removal['features']:  # add properties
            f['properties'] = {'z_value': 0, 'elev_cm': 0, 'elev': 0, 'DN': 0, 'DN_1': 1}
        geojson = json.dumps(polygons_for_removal)
        imageNames = ['_binary', '_binary_closing_edge_detection']  # index_binary, index_closing_edge_detection
        for iN in imageNames:
            if SHAPELINE_SOURCE_TYPE == 'cloning_and_cleaning':
                imgcleanup = GDALRaster(output_path + '/' + IMAGESET_NAME_INDEX_TYPE + '_cleanup' + iN + '.tif', write=True)
            else:
                imgcleanup = GDALRaster(output_path + '/' + IMAGESET_NAME_INDEX_TYPE + '' + iN + '.tif', write=True)
                # create a copy of the original file called cleanup
                shutil.copyfile(output_path + '/' + IMAGESET_NAME_INDEX_TYPE + '' + iN + '.tif', output_path + '/' + IMAGESET_NAME_INDEX_TYPE + '_cleanup' + iN + '.tif')
            w, h, e = imgcleanup.width, imgcleanup.height, imgcleanup.extent
            imgcleanup = None
            ext = [e[0], e[1], e[2], e[3]]
            imgmask = gdal.Rasterize(
                output_path +
                '/' +
                IMAGESET_NAME_INDEX_TYPE +
                '_cleanup_mask.tif',
                geojson,
                outputBounds=ext,
                width=w,
                height=h,
                allTouched=False,
                burnValues=1,
                outputType=gdal.GDT_Byte)
            imgmask_arr = imgmask.ReadAsArray()
            imgmask = None
            # change file
            imgcleanup = gdal.Open(output_path + '/' + IMAGESET_NAME_INDEX_TYPE + '_cleanup' + iN + '.tif', gdal.GA_Update)
            imgcleanup_arr = imgcleanup.GetRasterBand(1).ReadAsArray()
            print('Remove outside polygon? ' + str(remove_outside_polygon))
            if remove_outside_polygon is False:  # 1 is white (water)
                imgcleanup_arr = np.where(imgmask_arr == 1, 1, imgcleanup_arr)
            else:
                imgcleanup_arr = np.where(imgmask_arr == 1, imgcleanup_arr, 1)
            imgcleanup.GetRasterBand(1).WriteArray(imgcleanup_arr)
            imgcleanup = None
            imgcleanup_arr = None
            auxData2 = None

        # 3- Run worsica_ph6_vectorization.py with that image
        # difference does not copy the attributes to the new file, generates only the geometry,
        # you must copy the actual shp to a new one and affect geometry attribute
        shp_input = IMAGESET_NAME_INDEX_TYPE + ('_cleanup' if SHAPELINE_SOURCE_TYPE == 'cloning_and_cleaning' else '')
        print('3- Open ' + output_path + '/' + shp_input + '.shp')
        print(polygons_for_removal)
        gp = geopandas.read_file(output_path + '/' + shp_input + '.shp')
        gp3 = gp.copy()
        gpd = geopandas.GeoDataFrame.from_features(polygons_for_removal)
        if remove_outside_polygon is True:
            gp3['geometry'] = gp.intersection(gpd)
        else:
            gp3['geometry'] = gp.difference(gpd)
        shp_output = IMAGESET_NAME_INDEX_TYPE + '_cleanup'
        gp3.to_file(output_path + '/' + shp_output + '.shp')

        # 4- Store and replace binary image
        print('4- Store and replace binary image')
        INDEX_TYPE2 = INDEX_TYPE + ('_threshold' if actual_shapeline_group.isFromManualThreshold else '')
        itypes = [[INDEX_TYPE2 + '_cleanup_binary', INDEX_TYPE + '_bin'], [INDEX_TYPE2 + '_cleanup_binary_closing_edge_detection', INDEX_TYPE + '_ced']]
        for itype in itypes:
            subtasks._store_raster(pis, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, IMAGESET_NAME, False, False, itype, PATH_TO_PRODUCTS, False,
                                   False, False, SHAPELINE_SOURCE_TYPE, jsonReq['custom_name'])  # CLONE_AND_CLEAN_FROM=SHAPELINE_SOURCE_TYPE
        # 5- Store new shapeline
        print('5- Store new shapeline')
        subtasks._store_shapeline(pis, IMAGESET_NAME, INDEX_TYPE2 + '_cleanup', PATH_TO_PRODUCTS, SHAPELINE_SOURCE_TYPE, jsonReq['custom_name'])

        # 6- Store a zip
        print('6- Generate zip file')
        file_names = [
            shp_output + '_mask.tif',
            shp_output + '_binary.tif',
            shp_output + '_binary_closing_edge_detection.tif',
            shp_output + '.dbf',
            shp_output + '.prj',
            shp_output + '.shx',
            shp_output + '.shp']
        if INDEX_THRESHOLD == 'AUTO':  # has auto threshold
            file_names.append(IMAGESET_NAME_INDEX_TYPE + '_auto_threshold_val.txt')
        myfilenamezip = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/' + zipFileName2 + '-clone' + str(cloned_shapeline_group_id) + '.zip'
        print(myfilenamezip)
        WSF_CUSTOM = WORKSPACE_SIMULATION_FOLDER.replace('clone' + str(cloned_shapeline_group_id) + '/', '')  # remove cloneid
        with zipfile.ZipFile(myfilenamezip, mode='w') as zf:
            for file_name in file_names:
                if os.path.exists(output_path + '/' + file_name):
                    print(output_path + '/' + file_name)
                    zf.write(output_path + '/' + file_name, WSF_CUSTOM + '/' + IMAGESET_NAME + '_' + INDEX_TYPE + '_cleanup' + '/' + file_name, compress_type=zipfile.ZIP_DEFLATED)
        print('Start upload file directly to nextcloud')
        auth = (nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD)
        with open(myfilenamezip, 'rb') as f:
            retry, max_retries = 1, 5
            isUploadSuccess = False
            while retry <= max_retries and not isUploadSuccess:
                print('...attempt ' + str(retry))
                req = requests.put(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + zipFileName2 + '.zip', data=f, auth=auth)
                if (req.status_code == 201 or req.status_code == 204):
                    print('Upload successful')
                    isUploadSuccess = True
                else:
                    print('Upload error ' + str(req.status_code))
                    time.sleep(5)
                    retry += 1
            if (retry > max_retries):
                raise Exception('Upload failed')

        print('7- Remove old shp/rasters and rename the temporary ones')
        print('Remove old cleanup shp')
        rm_shapeline_group_id = None
        try:
            rm_shapeline_group = worsica_api_models.ShapelineMPIS.objects.get(
                mprocessImageSet=actual_shapeline_group.mprocessImageSet, name=actual_shapeline_group.name.replace(
                    '_cleanup', '') + '_cleanup', shapelineSourceType='cloning_and_cleaning')
            rm_shapeline_group_id = rm_shapeline_group.id
            rm_shapeline_group.delete()
            print('Removed cleanup shp')
        except Exception as e3:
            print('No old cleanup shp, skip')
            pass
        print('Rename tmp cleanup shp')
        cloned_shapeline_group.name = cloned_shapeline_group.name.replace('_tmp', '')
        cloned_shapeline_group.state = 'finished'
        cloned_shapeline_group.save()
        print('OK')
        itypes = [[INDEX_TYPE + '_cleanup_binary', INDEX_TYPE + '_bin'], [INDEX_TYPE + '_cleanup_binary_closing_edge_detection', INDEX_TYPE + '_ced']]
        for itype in itypes:
            print(itype[0])
            try:
                print('Remove binary and closing edge cleanup rasters')
                raster = worsica_api_models.MergedProcessImageSetRaster.objects.get(
                    name=SERVICE + '-u' + str(USER_ID) + '-r' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '-' +
                    itype[1] + ('-threshold' if actual_shapeline_group.isFromManualThreshold else '') + '-cleanup',
                    processImageSet=actual_shapeline_group.mprocessImageSet,
                    indexType=itype[0])
                raster.rasterLayer.delete()
                raster.delete()
            except Exception as e2:
                print('No binary and closing edge cleanup rasters')
                pass
            print('-----')
            try:
                print('Rename tmp binary and closing edge cleanup rasters')
                raster = worsica_api_models.MergedProcessImageSetRaster.objects.get(
                    name=SERVICE + '-u' + str(USER_ID) + '-r' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '-' +
                    itype[1] + ('-threshold' if actual_shapeline_group.isFromManualThreshold else '') + '-cleanup-tmp',
                    processImageSet=actual_shapeline_group.mprocessImageSet,
                    indexType=itype[0])
                raster.name = raster.name.replace('-tmp', '')
                raster.save()
            except Exception as e2:
                print('No tmp binary and closing edge cleanup rasters')
                pass
        # remove zip files
        shutil.rmtree(settings.WORSICA_FOLDER_PATH + '/worsica_web_products/clone' + str(cloned_shapeline_group_id) + '/')
        if (tmpPathZipFileName and os.path.exists(tmpPathZipFileName)):
            print('Remove zip input file')
            os.remove(tmpPathZipFileName)
        if (myfilenamezip and os.path.exists(myfilenamezip)):
            print('Remove zip output file')
            os.remove(myfilenamezip)

    except Exception as e:
        print(traceback.format_exc())
        print('Remove tmp shpline')
        try:
            cloned_shapeline_group.state = 'error-cloning'
            cloned_shapeline_group.save()
        except Exception as e2:
            print('Not found, skip')
            pass
        print('Remove tmp binary and closing edge rasters')
        itypes = [[INDEX_TYPE + '_cleanup_binary', INDEX_TYPE + '_bin'], [INDEX_TYPE + '_cleanup_binary_closing_edge_detection', INDEX_TYPE + '_ced']]
        for itype in itypes:
            try:
                print(itype[0])
                rasters = worsica_api_models.MergedProcessImageSetRaster.objects.filter(
                    name=SERVICE + '-u' + str(USER_ID) + '-r' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '-' +
                    itype[1] + ('-threshold' if actual_shapeline_group.isFromManualThreshold else '') + '-cleanup-tmp',
                    processImageSet=actual_shapeline_group.mprocessImageSet,
                    indexType=itype[0])
                for raster in rasters:
                    raster.rasterLayer.delete()
                    raster.delete()
            except Exception as e2:
                print('Not found, skip')
                pass
        shutil.rmtree(settings.WORSICA_FOLDER_PATH + '/worsica_web_products/clone' + str(cloned_shapeline_group_id) + '/')
        if (tmpPathZipFileName and os.path.exists(tmpPathZipFileName)):
            print('Remove zip input file')
            os.remove(tmpPathZipFileName)
        if (myfilenamezip and os.path.exists(myfilenamezip)):
            print('Remove zip output file')
            os.remove(myfilenamezip)


@csrf_exempt
def delete_cleanup_job_submission_shapeline(request, job_submission_id, process_imageset_id, shp_mpis_id):
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        try:
            pis = worsica_api_models.MergedProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
            print('has MergedProcessImageSet')
            actual_shapeline_group = worsica_api_models.ShapelineMPIS.objects.get(id=shp_mpis_id, mprocessImageSet=pis)
        except ObjectDoesNotExist:
            print('has ProcessImageSet')
            pis = worsica_api_models.ProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
            actual_shapeline_group = worsica_api_models.ShapelineMPIS.objects.get(id=shp_mpis_id, processImageSet=pis)
        # remove zip from nextcloud
        IMAGESET_NAME = pis.name
        IMAGESET_NAME_INDEX_TYPE = actual_shapeline_group.name.replace('_cleanup', '').replace('_threshold', '')
        INDEX_TYPE = IMAGESET_NAME_INDEX_TYPE.split('_')[-1]
        print('remove from nextcloud')
        SERVICE = job_submission.service
        USER_ID = job_submission.user_id
        ROI_ID = job_submission.aos_id
        SIMULATION_ID = job_submission.simulation_id
        WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)  # +'/cleanup'+str(actual_shapeline_group.id)
        zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '_' + INDEX_TYPE + '_cleanup-final-products'
        NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER
        r = requests.delete(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + zipFileName + '.zip', auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
        if (r.status_code == 204):
            worsica_logger.info('removed nextcloud ' + NEXTCLOUD_PATH_TO_PRODUCTS)
        print('Remove binary and closing edge rasters')
        itypes = [[INDEX_TYPE + '_cleanup_binary', INDEX_TYPE + '_bin'], [INDEX_TYPE + '_cleanup_binary_closing_edge_detection', INDEX_TYPE + '_ced']]
        for itype in itypes:
            try:
                print(itype[0])
                raster = worsica_api_models.MergedProcessImageSetRaster.objects.get(
                    name=SERVICE + '-u' + str(USER_ID) + '-r' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '-' +
                    itype[1] + ('-threshold' if actual_shapeline_group.isFromManualThreshold else '') + '-cleanup',
                    processImageSet=actual_shapeline_group.mprocessImageSet,
                    indexType=itype[0])
                raster.rasterLayer.delete()
                raster.delete()
            except Exception as e2:
                print('Not found, skip')
                pass
        print('delete shapeline group')
        actual_shapeline_group.delete()

        jsonResponse = {
            "alert": 'success',
            "indexType": INDEX_TYPE,
            "job_submission_id": job_submission_id,
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def download_cleanup_job_submission_shapeline(request, job_submission_id, process_imageset_id, shp_mpis_id):
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        try:
            pis = worsica_api_models.MergedProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
            print('has MergedProcessImageSet')
            actual_shapeline_group = worsica_api_models.ShapelineMPIS.objects.get(id=shp_mpis_id, mprocessImageSet=pis)
        except ObjectDoesNotExist:
            print('has ProcessImageSet')
            pis = worsica_api_models.ProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
            actual_shapeline_group = worsica_api_models.ShapelineMPIS.objects.get(id=shp_mpis_id, processImageSet=pis)
        # dl zip from nextcloud
        IMAGESET_NAME = pis.name
        IMAGESET_NAME_INDEX_TYPE = actual_shapeline_group.name.replace('_cleanup', '').replace('_threshold', '')
        INDEX_TYPE = IMAGESET_NAME_INDEX_TYPE.split('_')[-1]
        print('get download from nextcloud')
        SERVICE = job_submission.service
        USER_ID = job_submission.user_id
        ROI_ID = job_submission.aos_id
        SIMULATION_ID = job_submission.simulation_id
        WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)  # +'/cleanup'+str(actual_shapeline_group.id)
        zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '_' + INDEX_TYPE + '_cleanup-final-products'
        NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER
        return HttpResponse(json.dumps({'state': 'ok', 'url': str(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + zipFileName + '.zip'), 'appropriateFileName': zipFileName +
                                        '-final-products.zip', 'user': nextcloud_access.NEXTCLOUD_USER, 'pwd': nextcloud_access.NEXTCLOUD_PWD}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'state': 'error', 'error': 'worsica-intermediate:' + str(e)}), content_type='application/json')


@csrf_exempt
def download_threshold_job_submission(request, job_submission_id, process_imageset_id, raster_type):
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        try:
            print('has MergedProcessImageSet')
            pis = worsica_api_models.MergedProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
            iST = worsica_api_models.ImageSetThreshold.objects.get(processImageSet=pis, state='finished')
            actual_shapeline_group = worsica_api_models.ShapelineMPIS.objects.get(mprocessImageSet=pis, imageSetThreshold=iST)
        except ObjectDoesNotExist:
            print('has ProcessImageSet')
            pis = worsica_api_models.ProcessImageSet.objects.get(jobSubmission=job_submission, id=process_imageset_id)
            iST = worsica_api_models.ImageSetThreshold.objects.get(processImageSet=pis, state='finished')
            actual_shapeline_group = worsica_api_models.ShapelineMPIS.objects.get(processImageSet=pis, imageSetThreshold=iST)
        # dl zip from nextcloud
        IMAGESET_NAME = pis.name
        IMAGESET_NAME_INDEX_TYPE = actual_shapeline_group.name.replace('_threshold', '')
        print(IMAGESET_NAME_INDEX_TYPE)
        INDEX_TYPE = IMAGESET_NAME_INDEX_TYPE.split('_')[-1]
        print('get download from nextcloud')
        SERVICE = job_submission.service
        USER_ID = job_submission.user_id
        ROI_ID = job_submission.aos_id
        SIMULATION_ID = job_submission.simulation_id
        WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
        zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + IMAGESET_NAME + '_' + INDEX_TYPE + '_threshold-final-products'
        NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER
        return HttpResponse(json.dumps({'state': 'ok', 'url': str(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + zipFileName + '.zip'), 'appropriateFileName': zipFileName +
                                        '-final-products.zip', 'user': nextcloud_access.NEXTCLOUD_USER, 'pwd': nextcloud_access.NEXTCLOUD_PWD}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'state': 'error', 'error': 'worsica-intermediate:' + str(e)}), content_type='application/json')


# @login_required
@csrf_exempt
def delete_job_submission(request, job_submission_id):
    _host = request.get_host()
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = roi_id)
        worsica_logger.info('[run_shapeline]: delete id=' + str(job_submission_id) + ' state=' + job_submission.state)
        tasks.taskDeleteProcessing.delay(job_submission.aos_id, job_submission.id, _host)
        return HttpResponse(json.dumps({'state': 'deleted', 'roi_id': job_submission.aos_id, 'id': job_submission.id}), content_type='application/json')
    except Exception as e:
        return HttpResponse(json.dumps({'state': 'error', 'roi_id': job_submission.aos_id, 'id': job_submission.id, 'error': str(e)}), content_type='application/json')


def deleteProcessing(roi_id, job_submission_id, req_host):
    ROI_ID = roi_id
    job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
    if job_submission:
        # dataverses
        worsica_logger.info('[delete_simulation]: remove datasets')
        job_submission_dataverses = worsica_api_models.DataverseSubmission.objects.filter(jobSubmission=job_submission).order_by('-creationDate')
        if len(job_submission_dataverses) > 0:
            for jsd in job_submission_dataverses:
                worsica_logger.info('[delete_simulation]: remove ' + jsd.name)
                if jsd.doi is not None:
                    try:
                        dataverse_import.delete_dataset(dataverse_access.DATAVERSE_BASE_URL, dataverse_access.DATAVERSE_API_TOKEN, jsd.doi)
                    except Exception as e:
                        worsica_logger.info('[delete_simulation]: unable to remove on dataverse side, pass')
                        pass
                jsd.delete()
                worsica_logger.info('[delete_simulation]: removed')
        SERVICE = job_submission.service
        USER_ID = job_submission.user_id
        SIMULATION_ID = job_submission.simulation_id
        job_submission.isVisible = False
        job_submission.save()

        WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
        PATH_TO_PRODUCTS = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/' + WORKSPACE_SIMULATION_FOLDER
        NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER
        if os.path.exists(PATH_TO_PRODUCTS):
            shutil.rmtree(PATH_TO_PRODUCTS)
            worsica_logger.info('[delete_simulation]: remove ' + PATH_TO_PRODUCTS)
        # remove from nextcloud
        r = requests.delete(NEXTCLOUD_PATH_TO_PRODUCTS, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
        if (r.status_code == 204):
            worsica_logger.info('[delete_simulation]: remove nextcloud ' + NEXTCLOUD_PATH_TO_PRODUCTS)
        # mpis
        mpiss = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission)
        for mpis in mpiss:
            worsica_logger.info('[delete_simulation]: remove shp from ' + str(mpis.name))
            worsica_api_models.ShapelineMPIS.objects.filter(mprocessImageSet=mpis).delete()
        mpiss.delete()
        # pis
        piss = worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission)
        for pis in piss:
            worsica_logger.info('[delete_simulation]: remove shp from ' + str(pis.name))
            worsica_api_models.ShapelineMPIS.objects.filter(processImageSet=pis).delete()
        piss.delete()
        job_submission.delete()
        worsica_logger.info('[delete_simulation]: remove simulation ' + str(job_submission_id))
        return HttpResponse(json.dumps({'alert': 'deleted', 'simulation_id': SIMULATION_ID, "job_submission_id": job_submission_id}), content_type='application/json')
    else:
        worsica_logger.critical('[delete_simulation]: error remove simulation ')
        return HttpResponse(json.dumps({'alert': 'error', 'simulation_id': SIMULATION_ID, "job_submission_id": job_submission_id}), content_type='application/json')

# USER REPOSITORY


def _create_user_repository(user_id):
    try:
        # create user repository object
        ur, ur_created = worsica_api_models.UserRepository.objects.get_or_create(
            portal_user_id=user_id,
            name='Portal User ID ' + str(user_id),
            reference='user_' + str(user_id))
        # avoid constantly making folders if they do already exist (unless they are maliciously deleted
        # maybe do a check folder before creating?
        if ur_created:
            requests_session = requests.Session()
            requests_session.auth = (nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD)
            path = nextcloud_access.NEXTCLOUD_URL_PATH
            # create folder recursively
            pathList = ['/user_repository', '/user' + str(user_id)]  # create first the user_repository
            for p in pathList:
                path = path + p
                print(path)
                r = requests_session.request('MKCOL', path)
                if (r.status_code == 201 or r.status_code == 405):
                    print('[_create_user_repository]: folder ' + path + ' does not exist, create')
            # reaching /user_repository/userID, start creating subfolders
            # imagesets will be for all services
            subfolders = ['/imagesets']
            for sf in subfolders:
                pathsf = path + sf
                print(pathsf)
                r = requests_session.request('MKCOL', pathsf)
                if (r.status_code == 201 or r.status_code == 405):
                    print('[_create_user_repository]: folder ' + pathsf + ' does not exist, create')
            # create other folders
            # geometries will be loaded by each service
            # masks and leakpoints is only for waterleak service
            subfolders = ['/geometries', '/masks', '/leakpoints']
            for sf in subfolders:
                pathsf = path + sf
                print(pathsf)
                r = requests_session.request('MKCOL', pathsf)
                if (r.status_code == 201 or r.status_code == 405):
                    print('[_create_user_repository]: folder ' + pathsf + ' does not exist, create')
                #
                subsubfolders = ['/waterleak']
                if sf in ['/geometries']:  # geometries will for all services
                    subsubfolders += ['/inland', '/coastal']
                for ssf in subsubfolders:
                    pathsf2 = pathsf + ssf
                    print(pathsf2)
                    r = requests_session.request('MKCOL', pathsf2)
                    if (r.status_code == 201 or r.status_code == 405):
                        print('[_create_user_repository]: folder ' + pathsf2 + ' does not exist, create')
    except Exception as e:
        print(traceback.format_exc())


def _get_list_user_repository_by_service_coords(user_id, service_type, roi_polygon, show_only_uploaded_masks=False):
    response = {
        'alert': 'ok',
        'user_id': user_id
    }
    export_srid = 3857
    GEOJSON_AOS_POLYGON = GEOSGeometry("SRID=4326;" + roi_polygon)
    GEOJSON_AOS_POLYGON_TRANSF = GEOJSON_AOS_POLYGON.transform(export_srid, clone=True)

    list_of_ids_found_geoms = []
    most_recent_geom = None

    user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))

    jsonGeomsList = []
    geoms = worsica_api_models.UserRepositoryGeometry.objects.filter(user_repository=user_repository, service=service_type)
    for geom in geoms:
        if geom.wkt_bounds:
            if most_recent_geom is None or (most_recent_geom is not None and geom.uploadDate > most_recent_geom):
                most_recent_geom = geom.uploadDate
            list_of_ids_found_geoms.append(geom.id)
            geom_wkt_bounds = subsubtasks._normalize_wkt(geom.wkt_bounds)
            GEOJSON_GEOM_POLYGON = GEOSGeometry(geom_wkt_bounds)
            GEOJSON_GEOM_POLYGON_TRANSF = GEOJSON_GEOM_POLYGON.transform(export_srid, clone=True)
            jsonGeomsList.append({'id': geom.id, 'name': geom.name, 'state': geom.state, 'compatible': GEOJSON_AOS_POLYGON_TRANSF.intersects(GEOJSON_GEOM_POLYGON_TRANSF)})
    response['geometries'] = jsonGeomsList

    # imagesets
    jsonImagesetsList = []
    response['imagesets'] = jsonImagesetsList

    if (service_type == 'waterleak'):
        # masks
        jsonMasksList = []
        if (show_only_uploaded_masks):  # show only uploaded
            masks = worsica_api_models.UserRepositoryLeakDetectionMask.objects.filter(user_repository=user_repository, type_mask='uploaded_by_user')
        else:  # show all
            masks = worsica_api_models.UserRepositoryLeakDetectionMask.objects.filter(user_repository=user_repository)
        for mask in masks:
            if mask.wkt_bounds:
                mask_wkt_bounds = subsubtasks._normalize_wkt(mask.wkt_bounds)  # remove space after ;
                GEOJSON_MASK_POLYGON = GEOSGeometry(mask_wkt_bounds)
                GEOJSON_MASK_POLYGON_TRANSF = GEOJSON_MASK_POLYGON.transform(export_srid, clone=True)
                maskrasterlayer_id = None
                maskrasters = worsica_api_models.UserRepositoryLeakDetectionMaskRaster.objects.filter(user_repository_mask=mask)
                if len(maskrasters) > 0:  # always get the first
                    if maskrasters[0].maskRasterLayer:
                        maskrasterlayer_id = maskrasters[0].maskRasterLayer.id
                lmask = {'id': mask.id, 'name': mask.name, 'type_mask': mask.type_mask, 'state': mask.state,
                         'raster_id': maskrasterlayer_id, 'compatible': GEOJSON_AOS_POLYGON_TRANSF.intersects(GEOJSON_MASK_POLYGON_TRANSF)}
                if mask.type_mask == 'generated_from_shps':
                    # check if the list of ids from generated do match with the list of ids of found geometries for that roi
                    # hide this mask if no geoms were loaded
                    is_updated = False
                    if mask.generated_from_ids is not None and list_of_ids_found_geoms:  # not empty
                        mask_generated_from_ids = [int(s) for s in mask.generated_from_ids.split(',')]
                        is_updated = ((mask_generated_from_ids == list_of_ids_found_geoms) and mask.uploadDate >= most_recent_geom)
                    lmask['is_updated'] = is_updated
                jsonMasksList.append(lmask)
        response['masks'] = jsonMasksList
        # leaks
        jsonLeaksList = []
        leaks = worsica_api_models.UserRepositoryLeakDetectionLeak.objects.filter(user_repository=user_repository)
        for leak in leaks:
            if leak.wkt_bounds:
                leak_wkt_bounds = subsubtasks._normalize_wkt(leak.wkt_bounds)
                GEOJSON_LEAK_POLYGON = GEOSGeometry(leak_wkt_bounds)  # leak_wkt_bounds_split[0]+';'+leak_polygon)
                GEOJSON_LEAK_POLYGON_TRANSF = GEOJSON_LEAK_POLYGON.transform(export_srid, clone=True)
                jsonLeaksList.append({'id': leak.id, 'name': leak.name, 'state': leak.state, 'compatible': GEOJSON_AOS_POLYGON_TRANSF.intersects(GEOJSON_LEAK_POLYGON_TRANSF)})
        response['leaks'] = jsonLeaksList
    return response


@csrf_exempt
def list_user_repository_by_roi(request, user_id):
    try:
        if (request.GET.get('service_type')):
            service_type = request.GET.get('service_type')
            if (request.GET.get('roi_polygon')):
                coords = request.GET.get('roi_polygon').split('|')
                print(service_type)
                print(coords)
                uY, uX, lY, lX = coords
                roi_polygon = 'POLYGON ((' + uY + ' ' + uX + ',' + uY + ' ' + lX + ',' + lY + ' ' + lX + ',' + lY + ' ' + uX + ',' + uY + ' ' + uX + '))'
                _create_user_repository(user_id)
                response = _get_list_user_repository_by_service_coords(user_id, service_type, roi_polygon)

                return HttpResponse(json.dumps(response), content_type='application/json')
            else:
                return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'error': 'Missing roi_polygon!'}), content_type='application/json')
        else:
            return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'error': 'Missing service_type!'}), content_type='application/json')

    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'error': str(e)}), content_type='application/json')


@csrf_exempt
def get_user_leak_points_leak_detection(request, job_submission_id, leak_detection_id):
    _host = request.get_host()
    try:
        juls = []
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(jobSubmission=job_submission, id=leak_detection_id)
        user_leaks = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSaved.objects.filter(leak_detection=leak_detection)
        for ul in user_leaks:
            juls.append({'id': ul.id, 'name': ul.name, 'number_points': ul.number_points})
        return HttpResponse(json.dumps({'alert': 'success', 'job_submission_id': job_submission_id, 'leak_detection_id': leak_detection_id, 'user_leaks': juls}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'job_submission_id': job_submission_id, 'leak_detection_id': leak_detection_id, 'error': str(e)}), content_type='application/json')


@csrf_exempt
def save_user_leak_points_leak_detection(request, job_submission_id, leak_detection_id):
    try:
        jsonReq = json.loads(request.body.decode('utf-8'))
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
        leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(jobSubmission=job_submission, id=leak_detection_id)
        # user leak
        user_leaks = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSaved.objects.create(
            leak_detection=leak_detection,
            name=jsonReq['name'], small_name=jsonReq['name'],
            number_points=int(jsonReq['number_points']),
        )
        user_leaks.reference = job_submission.service + '-aos' + str(job_submission.aos_id) + '-simulation' + str(job_submission.simulation_id) + \
            '-job-submission' + str(job_submission.id) + '-ld' + str(leak_detection_id) + '-ul' + str(user_leaks.id)
        user_leaks.save()
        # user leak points
        # get from leak points
        leak_detection_out_type = ('anomaly_2nd_deriv' if leak_detection.start_processing_second_deriv_from == 'by_anomaly' else '2nd_deriv')
        rld = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.get(processImageSet=leak_detection, ldType=leak_detection_out_type)
        rlds_lp = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPoints.objects.filter(second_deriv_raster=rld)
        with transaction.atomic():
            for idx, lp in enumerate(rlds_lp[:int(jsonReq['number_points'])]):
                user_leak_points = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPoints.objects.create(
                    user_leak_points=user_leaks,
                    xCoordinate=lp.xCoordinate,
                    yCoordinate=lp.yCoordinate,
                    pixelValue=lp.pixelValue,
                )
                user_leak_points.name = user_leaks.name + ' (' + str(idx) + ')'
                user_leak_points.small_name = user_leaks.small_name + ' (' + str(idx) + ')'
                user_leak_points.reference = user_leaks.reference + '-point' + str(idx)
                user_leak_points.save()
        # retrun created ul
        createdUserLeak = {
            "aos_id": job_submission.aos_id,
            "simulation_id": job_submission.simulation_id,
            "leak_detection_id": leak_detection.id,
            "id": job_submission.id,
            "name": user_leaks.name,
            "number_points": user_leaks.number_points,
        }
        return HttpResponse(json.dumps({"alert": "created", "user_leak": createdUserLeak}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({"alert": "error", "exception": "worsica-web-intermediate:" + str(e)}), content_type='application/json')


@csrf_exempt
def show_leak_points_leak_detection(request, job_submission_id, leak_detection_id):
    try:
        if (request.GET.get('number_points')):
            show_max_leak_points = int(request.GET.get('number_points'))
            job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
            leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(jobSubmission=job_submission, id=leak_detection_id)
            leak_detection_out_type = ('anomaly_2nd_deriv' if leak_detection.start_processing_second_deriv_from == 'by_anomaly' else '2nd_deriv')
            rld = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.get(processImageSet=leak_detection, ldType=leak_detection_out_type)
            rlds_lp = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPoints.objects.filter(second_deriv_raster=rld)
            jsonLeakPoints = [{'id': rld_lp.id, 'xCoordinate': rld_lp.xCoordinate, 'yCoordinate': rld_lp.yCoordinate,
                               'pixelValue': rld_lp.pixelValue, 'showLeakPoint': rld_lp.showLeakPoint} for rld_lp in rlds_lp[:show_max_leak_points]]
            return HttpResponse(json.dumps({"alert": 'success', "job_submission_id": job_submission_id,
                                            "leak_detection_id": leak_detection_id, "leak_points": jsonLeakPoints}), content_type='application/json')
        else:
            return HttpResponse(json.dumps({'alert': 'error', 'job_submission_id': job_submission_id,
                                            'leak_detection_id': leak_detection_id, 'error': 'Missing number_points!'}), content_type='application/json')

    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({"alert": "error", "exception": "worsica-web-intermediate:" + str(e)}), content_type='application/json')


@csrf_exempt
def show_user_leak_point_leak_detection(request, job_submission_id, leak_detection_id):
    try:
        if (request.GET.get('user_leak_id')):
            user_leak_id = int(request.GET.get('user_leak_id'))
            job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
            leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(jobSubmission=job_submission, id=leak_detection_id)
            raster = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.get(processImageSet=leak_detection, ldType__in=['2nd_deriv', 'anomaly_2nd_deriv'])
            rtbmeta = raster_models.RasterLayerBandMetadata.objects.get(rasterlayer_id=raster.rasterLayer.id, band=0)
            rtbmeta_min = rtbmeta.min
            rtbmeta_max = rtbmeta.max
            user_leaks = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSaved.objects.get(leak_detection=leak_detection, id=user_leak_id)
            if request.GET.get('edit') and request.GET.get('edit') == 'true':
                rlds_lp = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPoints.objects.filter(user_leak_points=user_leaks)
            else:
                rlds_lp = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPoints.objects.filter(user_leak_points=user_leaks, showLeakPoint=True)
            jsonLeakPoints = [{'id': rld_lp.id, 'xCoordinate': rld_lp.xCoordinate, 'yCoordinate': rld_lp.yCoordinate,
                               'pixelValue': rld_lp.pixelValue, 'showLeakPoint': rld_lp.showLeakPoint} for rld_lp in rlds_lp]
            return HttpResponse(json.dumps({"alert": 'success', "job_submission_id": job_submission_id, "leak_detection_id": leak_detection_id,
                                            "raster_id": raster.rasterLayer.id, "min": rtbmeta_min, "max": rtbmeta_max, "leak_points": jsonLeakPoints}), content_type='application/json')
        else:
            return HttpResponse(json.dumps({'alert': 'error', 'job_submission_id': job_submission_id,
                                            'leak_detection_id': leak_detection_id, 'error': 'Missing user_leak_id!'}), content_type='application/json')

    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({"alert": "error", "exception": "worsica-web-intermediate:" + str(e)}), content_type='application/json')

# delete single leak point


@csrf_exempt
def delete_a_user_leak_point_leak_detection(request, job_submission_id, leak_detection_id):
    try:
        if (request.GET.get('user_leak_id')):
            user_leak_id = int(request.GET.get('user_leak_id'))
            job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
            leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(jobSubmission=job_submission, id=leak_detection_id)
            user_leak = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSaved.objects.get(leak_detection=leak_detection, id=user_leak_id)
            if (request.GET.get('delete_leak_points')):
                delete_leak_points_ids = request.GET.get('delete_leak_points').split(',')
                for point_leak_id in delete_leak_points_ids:
                    try:
                        user_leak_point = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPoints.objects.get(user_leak_points=user_leak, id=point_leak_id)
                        user_leak_point.delete()
                    except Exception as e:
                        print('Not found, skip')
                        pass
                user_leak.number_points = int(worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPoints.objects.filter(user_leak_points=user_leak).count())
                user_leak.save()
                return HttpResponse(json.dumps({"alert": 'deleted', "job_submission_id": job_submission_id, "leak_detection_id": leak_detection_id, }), content_type='application/json')
            else:
                # empty?
                return HttpResponse(json.dumps({'alert': 'deleted', 'job_submission_id': job_submission_id, 'leak_detection_id': leak_detection_id,
                                                'warning': 'Empty delete_leak_points!'}), content_type='application/json')

        else:
            return HttpResponse(json.dumps({'alert': 'error', 'job_submission_id': job_submission_id,
                                            'leak_detection_id': leak_detection_id, 'error': 'Missing user_leak_id!'}), content_type='application/json')

    except Exception as e:
        return HttpResponse(json.dumps({"alert": "error", "exception": "worsica-web-intermediate:" + str(e)}), content_type='application/json')

# delete all


@csrf_exempt
def delete_user_leak_points_leak_detection(request, job_submission_id, leak_detection_id):
    try:
        if (request.GET.get('user_leak_id')):
            user_leak_id = int(request.GET.get('user_leak_id'))
            job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
            leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(jobSubmission=job_submission, id=leak_detection_id)
            user_leaks = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSaved.objects.get(leak_detection=leak_detection, id=user_leak_id)
            user_leaks.delete()
            return HttpResponse(json.dumps({"alert": 'deleted', "job_submission_id": job_submission_id, "leak_detection_id": leak_detection_id, }), content_type='application/json')
        else:
            return HttpResponse(json.dumps({'alert': 'error', 'job_submission_id': job_submission_id,
                                            'leak_detection_id': leak_detection_id, 'error': 'Missing user_leak_id!'}), content_type='application/json')

    except Exception as e:
        return HttpResponse(json.dumps({"alert": "error", "exception": "worsica-web-intermediate:" + str(e)}), content_type='application/json')


@csrf_exempt
def edit_user_leak_point_leak_detection(request, job_submission_id, leak_detection_id):
    try:
        if (request.GET.get('user_leak_id')):
            user_leak_id = int(request.GET.get('user_leak_id'))
            job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
            leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(jobSubmission=job_submission, id=leak_detection_id)
            user_leak = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSaved.objects.get(leak_detection=leak_detection, id=user_leak_id)
            if (request.GET.get('point_leak_id')):
                point_leak_id = request.GET.get('point_leak_id')
                user_leak_point = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPoints.objects.get(user_leak_points=user_leak, id=point_leak_id)
                user_leak_point.showLeakPoint = (not user_leak_point.showLeakPoint)
                user_leak_point.save()
                jsonLeakPoint = {
                    'id': user_leak_point.id,
                    'xCoordinate': user_leak_point.xCoordinate,
                    'yCoordinate': user_leak_point.yCoordinate,
                    'pixelValue': user_leak_point.pixelValue,
                    'showLeakPoint': user_leak_point.showLeakPoint}
                return HttpResponse(json.dumps({"alert": 'success', "job_submission_id": job_submission_id,
                                                "leak_detection_id": leak_detection_id, "leak_point": jsonLeakPoint}), content_type='application/json')
            else:
                return HttpResponse(json.dumps({'alert': 'error', 'job_submission_id': job_submission_id,
                                                'leak_detection_id': leak_detection_id, 'error': 'Missing point_leak_id!'}), content_type='application/json')
        else:
            return HttpResponse(json.dumps({'alert': 'error', 'job_submission_id': job_submission_id,
                                            'leak_detection_id': leak_detection_id, 'error': 'Missing user_leak_id!'}), content_type='application/json')

    except Exception as e:
        return HttpResponse(json.dumps({"alert": "error", "exception": "worsica-web-intermediate:" + str(e)}), content_type='application/json')


@csrf_exempt
def download_user_leak_point_leak_detection(request, job_submission_id, leak_detection_id):
    try:
        if (request.GET.get('user_leak_id')):
            user_leak_id = int(request.GET.get('user_leak_id'))
            job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
            leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(jobSubmission=job_submission, id=leak_detection_id)

            # determine_leak: by_index or by_anomaly
            # leak_detection_output_type: anomaly, 2nd_deriv or anomaly_2nd_deriv
            determine_leak = leak_detection.start_processing_second_deriv_from
            raster_type = leak_detection.indexType
            if leak_detection.image_source_from == 'processed_image':
                pis_name = leak_detection.processImageSet.name
                ldr = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.get(processImageSet__processImageSet=leak_detection.processImageSet)
                leak_detection_out_type = ldr.ldType
            else:
                pis_name = leak_detection.ucprocessImageSet.name
                ldr = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.get(processImageSet__ucprocessImageSet=leak_detection.ucprocessImageSet)
                leak_detection_out_type = ldr.ldType
            filename = job_submission.name + '-' + pis_name + '-' + raster_type + '-' + leak_detection_out_type + '-' + determine_leak + '.kml'
            kml = "<?xml version='1.0' encoding='UTF-8'?>\n"
            kml += "<kml xmlns='http://www.opengis.net/kml/2.2'>\n"
            kml += '<Document>\n'
            kml += "<name>" + filename + "</name>\n"
            user_leak = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSaved.objects.get(leak_detection=leak_detection, id=user_leak_id)

            rlds_lp = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPoints.objects.filter(user_leak_points=user_leak, showLeakPoint=True).order_by('id')
            for dlp in rlds_lp:
                kml += "<Placemark>\n"
                kml += "<name>Leak point " + str(dlp.id) + "</name>\n"
                kml += "<ExtendedData><Data name='pixelValue'><value>" + str(dlp.pixelValue) + "</value></Data></ExtendedData>\n"
                point = OGRGeometry('SRID=3857;POINT(' + str(dlp.xCoordinate) + ' ' + str(dlp.yCoordinate) + ')')
                point.transform(4326)
                kml += "<Point><coordinates>" + str(point.x) + ',' + str(point.y) + ",0</coordinates></Point>\n"
                kml += "</Placemark>\n"
            kml += '</Document>\n'
            kml += "</kml>"
            return HttpResponse(json.dumps({'state': 'ok', 'filename': filename, 'kml': kml}), content_type='application/vnd.google-earth.kml+xml')

    except Exception as e:
        return HttpResponse(json.dumps({"alert": "error", "exception": "worsica-web-intermediate:" + str(e)}), content_type='application/json')


@csrf_exempt
def clone_user_leak_points_leak_detection(request, job_submission_id, leak_detection_id):
    try:
        jsonReq = json.loads(request.body.decode('utf-8'))
        if ('user_leak_id' in jsonReq):
            user_leak_id = int(jsonReq['user_leak_id'])
            job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)  # , aos_id = aos_id)
            leak_detection = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(jobSubmission=job_submission, id=leak_detection_id)
            user_leak = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSaved.objects.get(leak_detection=leak_detection, id=user_leak_id)
            if ('exclude_leak_points' in jsonReq and jsonReq['exclude_leak_points'] != ""):
                exclude_leak_points = jsonReq['exclude_leak_points'].split(',')
                user_leak_points = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPoints.objects.filter(user_leak_points=user_leak).exclude(id__in=exclude_leak_points)
            else:
                user_leak_points = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPoints.objects.filter(user_leak_points=user_leak)

            new_user_leak = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSaved.objects.create(
                leak_detection=leak_detection,
                name=jsonReq['name'], small_name=jsonReq['name'],
                number_points=int(user_leak_points.count())
            )
            for idx, user_leak_point in enumerate(user_leak_points):
                print(idx)
                print(user_leak_point.showLeakPoint)
                new_user_leak_points = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPoints.objects.create(
                    user_leak_points=new_user_leak,
                    xCoordinate=user_leak_point.xCoordinate,
                    yCoordinate=user_leak_point.yCoordinate,
                    pixelValue=user_leak_point.pixelValue,
                    showLeakPoint=user_leak_point.showLeakPoint,
                )
                new_user_leak_points.name = new_user_leak.name + ' (' + str(idx) + ')'
                new_user_leak_points.small_name = new_user_leak.small_name + ' (' + str(idx) + ')'
                new_user_leak_points.reference = new_user_leak.reference + '-point' + str(idx)
                new_user_leak_points.save()

            #
            jsonPisLeakRasterResponse = []
            new_user_leak_points = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPoints.objects.filter(user_leak_points=new_user_leak)  # .order_by('id')
            if len(new_user_leak_points) > 0:  # has points
                jsonPisLeakRasterResponse.append({
                    'type': leak_detection.indexType,
                    'start_processing_second_deriv_from': leak_detection.start_processing_second_deriv_from,
                    'lp_user_saved': True,
                    'lp_user_saved_id': new_user_leak.id,
                    'ldType': 'uslp' + str(new_user_leak.id) + '_leak_points',
                    'name': leak_detection.get_indexType_display() + ' Leak Points (' + new_user_leak.name + ')',
                    'roi_id': job_submission.aos_id,
                    'simulation_id': job_submission.simulation_id,
                    'intermediate_process_imageset_id': (leak_detection.processImageSet.id if leak_detection.processImageSet else None),
                    'intermediate_ucprocess_imageset_id': (leak_detection.ucprocessImageSet.id if leak_detection.ucprocessImageSet else None),
                    'intermediate_leak_id': leak_detection.id,
                    'raster_id': None,
                })

            return HttpResponse(json.dumps({"alert": 'cloned', "job_submission_id": job_submission_id, "leak_detection_id": leak_detection_id,
                                            "outputs": jsonPisLeakRasterResponse}), content_type='application/json')
        else:
            return HttpResponse(json.dumps({'alert': 'error', 'job_submission_id': job_submission_id,
                                            'leak_detection_id': leak_detection_id, 'error': 'Missing user_leak_id!'}), content_type='application/json')

    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({"alert": "error", "exception": "worsica-web-intermediate:" + str(e)}), content_type='application/json')


# USER REPOSITORY
@csrf_exempt
def show_user_repository(request, user_id, service_type):
    try:
        _create_user_repository(user_id)
        user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
        # list geometries
        geoms = worsica_api_models.UserRepositoryGeometry.objects.filter(user_repository=user_repository, service=service_type)
        jsonGeomsList = [{'id': geom.id, 'name': geom.name, 'state': geom.state} for geom in geoms]
        # list imagesets
        imagesets = worsica_api_models.UserRepositoryImageSets.objects.filter(user_repository=user_repository)
        jsonImagesetsList = [{'id': imageset.id, 'name': imageset.name, 'state': imageset.state} for imageset in imagesets]
        response = {'alert': 'ok',
                    'user_id': user_id,
                    'geometries': jsonGeomsList,
                    'imagesets': jsonImagesetsList}
        if (service_type == 'waterleak'):
            # list masks
            masks = worsica_api_models.UserRepositoryLeakDetectionMask.objects.filter(user_repository=user_repository, type_mask='uploaded_by_user')
            jsonMasksList = []
            for mask in masks:
                maskrasterlayer_id = None
                maskrasters = worsica_api_models.UserRepositoryLeakDetectionMaskRaster.objects.filter(user_repository_mask=mask)
                if len(maskrasters) > 0:
                    # always get the first
                    if maskrasters[0].maskRasterLayer:
                        maskrasterlayer_id = maskrasters[0].maskRasterLayer.id
                jsonMasksList.append({'id': mask.id, 'name': mask.name, 'type_mask': mask.type_mask, 'state': mask.state, 'raster_id': maskrasterlayer_id})
            response['masks'] = jsonMasksList
            # list leak points
            leaks = worsica_api_models.UserRepositoryLeakDetectionLeak.objects.filter(user_repository=user_repository)
            jsonLeaksList = []
            for leak in leaks:
                jsonLeaksList.append({'id': leak.id, 'name': leak.name, 'state': leak.state})
            response['leaks'] = jsonLeaksList
        return HttpResponse(json.dumps(response), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'error': str(e)}), content_type='application/json')


@csrf_exempt
def preview_user_repository_geometry(request, service_type, user_id):
    if request.method == 'POST':
        try:
            myfile = request.FILES['myfile']
            myfilenametmp = myfile.file.name  # file path name
            myfilenamesplit = myfile.file.name.split('.')[0]
            myfilename = slugify(myfilenamesplit)
            print('filename: ' + myfilename)
            print('tmpfile: ' + str(myfilenametmp))
            feature_collection = {
                'type': 'FeatureCollection',
                'crs': {
                        'type': 'name',
                        'properties': {'name': 'EPSG:4326'}
                },
                'features': []
            }
            zf = zipfile.ZipFile(myfilenametmp, 'r')
            zf.extractall(myfilenamesplit)
            for file_name in zf.namelist():
                if file_name.endswith('.shp'):
                    shp_i = 0
                    shp_path = file_name
                    print(myfilenamesplit + '/' + shp_path)
                    ds = DataSource(myfilenamesplit + '/' + shp_path)
                    for n in range(ds.layer_count):
                        # Transform the coordinates to epsg:4326
                        features = map(lambda geom: geom.transform(4326, clone=True), ds[n].get_geoms())
                        for feature_i, feature in enumerate(features):
                            featurej = json.loads(feature.json)
                            _featcoordinates = [[f[1], f[0]] for f in featurej['coordinates']]
                            featurej['coordinates'] = _featcoordinates
                            feature_collection['features'].append(
                                {
                                    'type': 'Feature',
                                    'geometry': featurej,
                                    'properties': {
                                            'name': f'shapefile_{shp_i}_feature_{feature_i}'
                                    }
                                }
                            )
                    shutil.rmtree(myfilenamesplit)
            return HttpResponse(json.dumps({'alert': 'success', 'feature_collection': feature_collection}), content_type='application/json')

        except Exception as e:
            print(traceback.format_exc())
            return HttpResponse(json.dumps({'alert': 'error', 'message': 'An error occured during upload', 'details': str(e)}), content_type='application/json')
    else:
        return HttpResponse(json.dumps({'alert': 'error', 'message': 'Not a POST request, abort'}), content_type='application/json')


@csrf_exempt
def upload_user_repository_geometry(request, service_type, user_id):
    data = json.loads(request.body.decode('utf-8'))
    myfilename = data['filename']
    myfilename_noext = myfilename[:-4]
    # create user repository geometry
    worsica_logger.info('[upload_user_repository_geometry]: create user repository geometry')
    user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
    geom, geom_created = worsica_api_models.UserRepositoryGeometry.objects.get_or_create(
        user_repository=user_repository,
        service=service_type,
        name=myfilename_noext,
        small_name=myfilename_noext)
    if geom_created:
        geom.small_name = myfilename_noext
    geom.uploadDate = datetime.datetime.now()
    geom.state = 'uploading'
    geom.save()

    fnshp = None
    nc_path = nextcloud_access.NEXTCLOUD_URL_PATH + '/user_repository/user' + (user_id) + '/geometries/' + service_type + '/' + myfilename

    # upload
    try:
        # create the folder (if does not exist)
        _create_user_repository(user_id)
        # store uploaded file to tmp
        worsica_logger.info('[upload_user_repository_geometry]: download file to tmp')
        auth = (nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD)
        r = requests.get(nc_path, auth=auth)
        tmppath = '/tmp/user_repository/user' + str(user_id) + '/geometries/' + service_type
        if r.status_code == 200:
            os.makedirs(tmppath, exist_ok=True)
            with open(tmppath + '/' + myfilename, 'wb') as f:
                f.write(r.content)
        # do the validation file checks
        # check if has shp file
        # store the geometry to the db
        worsica_logger.info('[upload_user_repository_geometry]: open zip file')
        worsica_logger.info('[upload_user_repository_geometry]: extract all files to ' + tmppath + '/' + myfilename)
        worsica_logger.info('[upload_user_repository_geometry]: check if has shp file')
        zf = zipfile.ZipFile(tmppath + '/' + myfilename, 'r')
        zf.extractall(tmppath + '/' + myfilename_noext)
        hasSHP, hasDBF, hasSHX = False, False, False
        for file_name in zf.namelist():
            if file_name.endswith('.shp'):
                fnshp = file_name
                hasSHP = True
            if file_name.endswith('.dbf'):
                hasDBF = True
            if file_name.endswith('.shx'):
                hasSHX = True
        if (hasSHP and hasDBF and hasSHX):
            print(fnshp)
            geom.state = 'uploaded'
            geom.save()
        else:
            geom.state = 'error-uploading'
            geom.save()
            raise Exception('Sorry, this ZIP file must have at least shp, dbf and shx files.')
    except Exception as e:
        geom.delete()
        # delete the invalid file
        worsica_logger.info('[upload_user_repository_geometry]: invalid file!')
        r = requests.delete(nc_path, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
        if (r.status_code == 204):
            worsica_logger.info('[upload_user_repository_geometry]: remove file ' + nc_path)
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'geometry_id': geom.id, 'message': str(e)}), content_type='application/json')

    # store
    try:
        worsica_api_models.UserRepositoryGeometryShapeline.objects.filter(user_repository_geom=geom).delete()
        geom.state = 'storing'
        geom.save()
        if fnshp is None:
            geom.state = 'error-storing'
            geom.save()
            return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'geometry_id': geom.id, 'message': 'No shp file found'}), content_type='application/json')
        else:
            print('Datasource check')
            shpfile = tmppath + '/' + myfilename_noext + '/' + fnshp
            ds = DataSource(shpfile)
            geom.wkt_bounds = '"SRID=' + str(ds[0].srs.srid) + ';' + str(ds[0].extent.wkt) + '"'
            geom.save()

            def pre_save_shapeline_callback(sender, instance, *args, **kwargs):
                instance.user_repository_geom = geom
                instance.name = 'user' + str(user_id) + '-geometry' + str(geom.id) + '-' + slugify(geom.name)

            worsica_logger.info('[upload_user_repository_geometry]: Start import shapeline!')
            shp_mapping = {'mls': 'LINESTRING'}

            lm = LayerMapping(worsica_api_models.UserRepositoryGeometryShapeline, shpfile, shp_mapping, transform=True, encoding='utf-8', )
            pre_save.connect(pre_save_shapeline_callback, sender=worsica_api_models.UserRepositoryGeometryShapeline)
            lm.save(verbose=True)
            pre_save.disconnect(pre_save_shapeline_callback, sender=worsica_api_models.UserRepositoryGeometryShapeline)
            geom.state = 'stored'
            geom.save()
            return HttpResponse(json.dumps({'alert': 'uploaded', 'user_id': user_id, 'geometry_id': geom.id, }), content_type='application/json')
    except Exception as e:
        geom.state = 'error-storing'
        geom.save()
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'geometry_id': geom.id, 'message': str(e)}), content_type='application/json')


@csrf_exempt
def show_user_repository_geometry(request, user_id, service_type, geometry_id):
    try:
        features = []
        user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
        geom = worsica_api_models.UserRepositoryGeometry.objects.get(user_repository=user_repository, service=service_type, id=int(geometry_id))
        shplines = worsica_api_models.UserRepositoryGeometryShapeline.objects.filter(user_repository_geom=geom)
        for shpline in shplines:
            shp_mls = shpline.mls  # .convex_hull
            feature = json.loads(shp_mls.json)
            # gdal 3.0.4 must have changed the way shp are generated
            # instead of lonlat, they are generated as latlon, causing issues on loading them to map
            # this is a workaround to fix that by passing latlon to lonlat
            _featcoordinates = [[[f[1], f[0]] for f in feat]
                                for feat in feature['coordinates']]
            feature['coordinates'] = _featcoordinates
            features.append({'type': 'Feature', 'geometry': feature})
        fc = {
            'type': 'FeatureCollection',
            'crs': {
                    'type': 'name',
                    'properties': {
                            'name': 'EPSG:4326'
                    }
            },
            'features': features
        }
        jsonGeomList = {'alert': 'ok',
                        'user_id': user_id,
                        'geometry_id': geometry_id,
                        'name': geom.name,
                        'state': geom.state,
                        'json': fc
                        }
        return HttpResponse(json.dumps(jsonGeomList), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'geometry_id': geometry_id, 'error': str(e)}), content_type='application/json')


@csrf_exempt
def delete_user_repository_geometry(request, user_id, service_type, geometry_id):
    try:
        user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
        geom = worsica_api_models.UserRepositoryGeometry.objects.get(user_repository=user_repository, id=int(geometry_id))
        # delete file
        auth = (nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD)
        path = nextcloud_access.NEXTCLOUD_URL_PATH + '/user_repository/user' + str(user_id) + '/geometries/' + service_type + '/' + geom.name + '.zip'
        r = requests.delete(path, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
        if (r.status_code == 204):
            worsica_logger.info('[delete_simulation]: remove file ' + path)
        # delete db shplines
        worsica_api_models.UserRepositoryGeometryShapeline.objects.filter(user_repository_geom=geom).delete()
        # delete db geom
        geom.delete()
        return HttpResponse(json.dumps({'alert': 'deleted', 'user_id': user_id, 'geometry_id': geometry_id}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'geometry_id': geometry_id, 'error': str(e)}), content_type='application/json')


@csrf_exempt
def upload_user_repository_mask(request, user_id, service_type):
    data = json.loads(request.body.decode('utf-8'))
    myfilename = data['filename']
    myfilename_noext = myfilename[:-4]
    # create user repository geometry
    worsica_logger.info('[upload_user_repository_mask]: create user repository geometry')
    user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
    ldmask, ldmask_created = worsica_api_models.UserRepositoryLeakDetectionMask.objects.get_or_create(
        user_repository=user_repository, name=myfilename_noext, small_name=myfilename_noext, reference=myfilename_noext,
        type_mask='uploaded_by_user')
    ldmask.uploadDate = datetime.datetime.now()
    ldmask.state = 'uploading'
    ldmask.save()

    nc_path = nextcloud_access.NEXTCLOUD_URL_PATH + '/user_repository/user' + (user_id) + '/masks/' + service_type + '/' + myfilename
    tmppath = '/tmp/user_repository/user' + str(user_id) + '/masks/' + service_type
    ZIP_FILE_PATH = tmppath + '/' + myfilename_noext + '.zip'
    TIF_FILE_PATH = tmppath + '/' + myfilename_noext + '/' + myfilename_noext + '.tif'
    # upload
    try:
        # create the folder (if does not exist)
        _create_user_repository(user_id)
        # store uploaded file to tmp
        worsica_logger.info('[upload_user_repository_mask]: download file to tmp')
        auth = (nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD)
        r = requests.get(nc_path, auth=auth)
        if r.status_code == 200:
            os.makedirs(tmppath, exist_ok=True)
            with open(ZIP_FILE_PATH, 'wb') as f:
                f.write(r.content)
        zf = zipfile.ZipFile(ZIP_FILE_PATH, 'r')
        zf.extractall(tmppath + '/' + myfilename_noext)
        # do the validation file checks
        # check if has tif is geotiff and has binary values
        worsica_logger.info('[upload_user_repository_mask]: open tif file')
        worsica_logger.info('[upload_user_repository_mask]: check if it is a geotiff file')
        rst = GDALRaster(TIF_FILE_PATH, write=False)
        if rst:
            rstband = rst.bands[0]
            rstbandmin, rstbandmax = rstband.min, rstband.max
            print(rstbandmin, rstbandmax)
            if (rstbandmin != 0 and (rstbandmax != 1 or rstbandmax != 255)):
                ldmask.state = 'error-uploading'
                ldmask.save()
                raise Exception('Not a binary image! Min and Max are [' + str(rstbandmin) + ',' + str(rstbandmax) + '] and must be [0,1] or [0,255]')
            else:
                # store wkt_bounds
                (x1, y1, x2, y2) = rst.extent
                polygon = "POLYGON ((" + str(x1) + " " + str(y1) + ", " + str(x1) + " " + str(y2) + ", " + str(x2) + \
                    " " + str(y2) + ", " + str(x2) + " " + str(y1) + ", " + str(x1) + " " + str(y1) + "))"
                srid = rst.srs.srid
                GEOJSON_MASK_POLYGON = GEOSGeometry("SRID=" + str(srid) + ";" + polygon)
                print(GEOJSON_MASK_POLYGON)
                ldmask.wkt_bounds = str(GEOJSON_MASK_POLYGON)
                ldmask.state = 'uploaded'
                ldmask.save()
        else:
            ldmask.state = 'error-uploading'
            ldmask.save()
            raise Exception('Not a valid geotiff file image!')
    except Exception as e:
        ldmask.delete()
        # delete the invalid file
        worsica_logger.info('[upload_user_repository_mask]: invalid file!')
        r = requests.delete(nc_path, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
        if (r.status_code == 204):
            worsica_logger.info('[upload_user_repository_mask]: remove file ' + nc_path)
            os.remove(ZIP_FILE_PATH)
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'mask_id': ldmask.id, 'message': str(e)}), content_type='application/json')

    # store
    try:
        worsica_api_models.UserRepositoryLeakDetectionMaskRaster.objects.filter(user_repository_mask=ldmask).delete()
        ldmask.state = 'storing'
        ldmask.save()

        wrapped_file = open(TIF_FILE_PATH, 'rb')
        nameRl = myfilename
        print('[startProcessing _store_raster] File ' + TIF_FILE_PATH + ' found, store it')
        try:
            print('[startProcessing _store_raster] check Rasterlayer ' + nameRl)
            rl = raster_models.RasterLayer.objects.get(name=nameRl)
            print('[startProcessing _store_raster] found it, delete')
            rl.delete()
            print('[startProcessing _store_raster] create again')
            rl = raster_models.RasterLayer.objects.create(name=nameRl)
        except Exception as e:
            print('[startProcessing _store_raster] not found, create')
            rl = raster_models.RasterLayer.objects.create(name=nameRl)
        rl.rasterfile.save('new', File(wrapped_file))
        rl.save()
        wrapped_file.close()
        wrapped_file = None
        os.remove(TIF_FILE_PATH)
        ldmaskraster, ldmaskraster_created = worsica_api_models.UserRepositoryLeakDetectionMaskRaster.objects.get_or_create(
            user_repository_mask=ldmask, name=ldmask.name, reference=ldmask.reference)
        ldmaskraster.maskRasterLayer = rl
        ldmaskraster.save()
        ldmask.state = 'stored'
        ldmask.save()
        return HttpResponse(json.dumps({'alert': 'uploaded', 'user_id': user_id, 'mask_id': ldmask.id, }), content_type='application/json')
    except Exception as e:
        ldmask.state = 'error-storing'
        ldmask.save()
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'mask_id': ldmask.id, 'message': str(e)}), content_type='application/json')


@csrf_exempt
def show_user_repository_mask(request, user_id, service_type, mask_id, raster_id):
    try:
        print(user_id, service_type, mask_id, raster_id)
        user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
        mask = worsica_api_models.UserRepositoryLeakDetectionMask.objects.get(user_repository=user_repository, id=int(mask_id))
        maskraster = worsica_api_models.UserRepositoryLeakDetectionMaskRaster.objects.get(user_repository_mask=mask, maskRasterLayer__id=int(raster_id))

        rtbmeta = raster_models.RasterLayerBandMetadata.objects.get(rasterlayer_id=maskraster.maskRasterLayer.id, band=0)
        rtbmeta_min = rtbmeta.min
        rtbmeta_max = rtbmeta.max
        jsonResponse = {
            "alert": 'success',
            "user_id": user_id,
            "mask_id": mask_id,
            "service_type": service_type,
            "raster_id": raster_id
        }
        rgb_rtbmeta_min = [0, 0, 0]
        rgb_rtbmeta_middle = [128, 128, 128]
        rgb_rtbmeta_max = [255, 255, 255]
        jsonResponse["response"] = {
            'url': '/raster/tiles/' + str(raster_id),
            'rgbcolorfrom': rgb_rtbmeta_min, 'min': rtbmeta_min,
            'rgbcolorto': rgb_rtbmeta_max, 'max': rtbmeta_max,
            'rgbcolorover': rgb_rtbmeta_middle
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(e)
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "user_id": user_id,
            "mask_id": mask_id,
            "service_type": service_type,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def delete_user_repository_mask(request, user_id, service_type, mask_id):
    try:
        user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
        mask = worsica_api_models.UserRepositoryLeakDetectionMask.objects.get(user_repository=user_repository, id=int(mask_id))
        # delete file
        auth = (nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD)
        path = nextcloud_access.NEXTCLOUD_URL_PATH + '/user_repository/user' + str(user_id) + '/masks/' + service_type + '/' + mask.name + '.zip'
        r = requests.delete(path, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
        if (r.status_code == 204):
            worsica_logger.info('[delete_simulation]: remove file ' + path)
        # delete db mask rasters
        worsica_api_models.UserRepositoryLeakDetectionMaskRaster.objects.filter(user_repository_mask=mask).delete()
        # delete db masks
        mask.delete()
        return HttpResponse(json.dumps({'alert': 'deleted', 'user_id': user_id, 'mask_id': mask_id}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'mask_id': mask_id,
                                        'error': 'Your leak detection failed to run! Something went wrong, try again!', 'details': str(e)}), content_type='application/json')


@csrf_exempt
def upload_user_repository_leak(request, user_id, service_type):
    data = json.loads(request.body.decode('utf-8'))
    myfilename = data['filename']
    myfilename_noext = myfilename[:-4]
    # create user repository geometry
    worsica_logger.info('[upload_user_repository_leak]: create user repository geometry')
    user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
    leak, leak_created = worsica_api_models.UserRepositoryLeakDetectionLeak.objects.get_or_create(
        user_repository=user_repository, name=myfilename_noext, small_name=myfilename_noext, reference=myfilename_noext)
    leak.uploadDate = datetime.datetime.now()
    leak.state = 'uploading'
    leak.save()

    nc_path = nextcloud_access.NEXTCLOUD_URL_PATH + '/user_repository/user' + (user_id) + '/leakpoints/' + service_type + '/' + myfilename
    tmppath = '/tmp/user_repository/user' + str(user_id) + '/leakpoints/' + service_type
    ZIP_FILE_PATH = tmppath + '/' + myfilename_noext + '.zip'
    KML_FILE_PATH = tmppath + '/' + myfilename_noext + '/' + myfilename_noext + '.kml'
    ds = None

    try:
        # create the folder (if does not exist)
        _create_user_repository(user_id)
        # store uploaded file to tmp
        worsica_logger.info('[upload_user_repository_leak]: download file to tmp')
        auth = (nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD)
        r = requests.get(nc_path, auth=auth)
        if r.status_code == 200:
            os.makedirs(tmppath, exist_ok=True)
            with open(ZIP_FILE_PATH, 'wb') as f:
                f.write(r.content)
        zf = zipfile.ZipFile(ZIP_FILE_PATH, 'r')
        zf.extractall(tmppath + '/' + myfilename_noext)
        # do the validation file checks
        # check if has tif is geotiff and has binary values
        worsica_logger.info('[upload_user_repository_leak]: open kml file')
        worsica_logger.info('[upload_user_repository_leak]: check if it is a valid file')
        ds = DataSource(KML_FILE_PATH)
        if ds:
            leak.state = 'uploaded'
            leak.save()
        else:
            raise Exception('Unable to read kml file because it is not valid!')

    except Exception as e:
        leak_id = leak.id
        leak.delete()
        # delete the invalid file
        worsica_logger.info('[upload_user_repository_leak]: invalid file!')
        r = requests.delete(nc_path, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
        if (r.status_code == 204):
            worsica_logger.info('[upload_user_repository_leak]: remove file ' + nc_path)
            os.remove(ZIP_FILE_PATH)
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'leak_id': leak_id, 'message': str(e)}), content_type='application/json')

    # store
    try:
        leak.state = 'storing'
        leak.save()
        worsica_logger.info('[upload_user_repository_leak]: delete previous leak points')
        if ds:
            worsica_api_models.UserRepositoryLeakDetectionLeakPoints.objects.filter(user_repository_leak=leak).delete()
            worsica_logger.info('[upload_user_repository_leak]: start loading leak points to db ')
            leak.wkt_bounds = '"SRID=' + str(ds[0].srs.srid) + ';' + str(ds[0].extent.wkt) + '"'
            leak.save()
            with transaction.atomic():
                for layer in ds:
                    for pt in layer:
                        dlp = pt.geom.tuple
                        point = OGRGeometry('SRID=4326;POINT(' + str(dlp[0]) + ' ' + str(dlp[1]) + ')')
                        point.transform(3857)
                        lp = worsica_api_models.UserRepositoryLeakDetectionLeakPoints.objects.create(
                            user_repository_leak=leak, name=pt.get('name'),
                            xCoordinate=float(point.x), yCoordinate=float(point.y),
                            pixelValue=float(pt.get('pixelValue')))
                        lp.small_name = pt.get('name')  # leak.small_name+'-leakid'+str(lp.id)
                        lp.save()

        # Remove the file
        if (os.path.exists(KML_FILE_PATH)):
            os.remove(KML_FILE_PATH)
        worsica_logger.info('[upload_user_repository_leak]: success')
        leak.state = 'stored'
        leak.save()
        return HttpResponse(json.dumps({'alert': 'uploaded', 'user_id': user_id, 'leak_id': leak.id, }), content_type='application/json')

    except Exception as e:
        leak.state = 'error-storing'
        leak.save()
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'leak_id': leak.id, 'message': str(e)}), content_type='application/json')


@csrf_exempt
def show_user_repository_leak(request, user_id, service_type, leak_id):
    try:
        print(user_id, service_type, leak_id)
        user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
        leak = worsica_api_models.UserRepositoryLeakDetectionLeak.objects.get(user_repository=user_repository, id=int(leak_id))
        leakpoints = worsica_api_models.UserRepositoryLeakDetectionLeakPoints.objects.filter(user_repository_leak=leak)
        jsonLeakPoints = [{'id': rld_lp.id, 'xCoordinate': rld_lp.xCoordinate, 'yCoordinate': rld_lp.yCoordinate,
                           'pixelValue': rld_lp.pixelValue, 'showLeakPoint': rld_lp.showLeakPoint} for rld_lp in leakpoints]
        jsonResponse = {
            "alert": 'success',
            "user_id": user_id,
            "leak_id": leak_id,
            "service_type": service_type,
            "leak_points": jsonLeakPoints,
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "user_id": user_id,
            "leak_id": leak_id,
            "service_type": service_type,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def delete_user_repository_leak(request, user_id, service_type, leak_id):
    try:
        user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
        leak = worsica_api_models.UserRepositoryLeakDetectionLeak.objects.get(user_repository=user_repository, id=int(leak_id))
        # delete file
        auth = (nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD)
        path = nextcloud_access.NEXTCLOUD_URL_PATH + '/user_repository/user' + str(user_id) + '/leakpoints/' + service_type + '/' + leak.name + '.zip'
        r = requests.delete(path, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
        if (r.status_code == 204):
            worsica_logger.info('[delete_simulation]: remove file ' + path)
        # delete db leak points
        worsica_api_models.UserRepositoryLeakDetectionLeakPoints.objects.filter(user_repository_leak=leak).delete()
        # delete db leak
        leak.delete()
        return HttpResponse(json.dumps({'alert': 'deleted', 'user_id': user_id, 'leak_id': leak_id}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'leak_id': leak_id, 'error': str(e)}), content_type='application/json')


@csrf_exempt
def probe_sea_tides(request):
    try:
        np.seterr(divide='ignore', invalid='ignore')
        pathData = "/usr/local/bin/FES2014/data/ocean_tide_extrapolated"
        jsonReq = json.loads(request.body.decode('utf-8'))

        print(jsonReq)
        pointAux = jsonReq['coords'].split(",")
        pointAux = [pointAux[1], pointAux[0]]
        pointAux2 = [float(i) for i in pointAux]
        point = tuple(pointAux2)
        zref = float(jsonReq['zref'])
        dateIni = datetime.datetime.strptime(jsonReq['beginDate'], '%Y-%m-%d')
        dateEnd = datetime.datetime.strptime(jsonReq['endDate'], '%Y-%m-%d')

        dt = 600.

        tide_fes2014 = ['2N2', 'EPS2', 'J1', 'K1', 'K2', 'L2',
                        'LAMBDA2', 'M2', 'M3', 'M4', 'M6', 'M8',
                        'MF', 'MKS2', 'MM', 'MN4', 'MS4', 'MSF',
                        'MSQM', 'MTM', 'MU2', 'N2', 'N4', 'NU2',
                        'O1', 'P1', 'Q1', 'R2', 'S1', 'S2', 'S4',
                        'SA', 'SSA', 'T2']

        delta_run = dateEnd.date() + datetime.timedelta(days=1) - dateIni.date()
        days = delta_run.days

        tide = uptide.Tides(tide_fes2014)
        tide.set_initial_time(dateIni)

        base = dateIni
        t = np.arange(0, days * 24 * 3600, dt)
        tarr = np.array([base + datetime.timedelta(seconds=round(i)) for i in t])

        tnci = uptide.FES2014TidalInterpolator(tide, pathData)

        eta = []
        data = [f"Location: {point[0]} N / {point[1]} W "]
        data2 = []
        for n, i in enumerate(t):
            tnci.set_time(int(i))
            eta.append(tnci.get_val(point) + zref)
            data.append(f"{tarr[n]}    {eta[-1][0]}")
            data2.append([int(datetime.datetime.strptime(str(tarr[n]), "%Y-%m-%d %H:%M:%S").timestamp()) * 1000, float(eta[-1][0])])
        dataSTR = "\n".join(data)
        encodedData = b64encode(str(dataSTR).encode('ascii')).decode('ascii')
        return HttpResponse(json.dumps({'alert': 'success', 'tide': data2, 'encodedData': encodedData}), content_type='application/json')

    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'error': str(e)}), content_type='application/json')


@csrf_exempt
def generate_topography(request, job_submission_id):
    _host = request.get_host()
    try:
        jsonReq = json.loads(request.body.decode('utf-8'))
        name = slugify(jsonReq['name'])
        opt = jsonReq['opt']
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        generateTopo = worsica_api_models.GenerateTopographyMap.objects.create(
            jobSubmission=job_submission, name=name, small_name=name, generateTopographyFrom='probing' if opt == 'optSeaTopoMap1' else 'upload')
        worsica_logger.info('[generate_topography]: run id=' + str(job_submission_id) + ' state=' + generateTopo.processState)
        task_identifier = get_task_identifier_generate_topo(job_submission, generateTopo.id)
        print(task_identifier)
        tasks.taskStartTopography.apply_async(args=[job_submission.aos_id, job_submission.id, generateTopo.id, jsonReq, _host], task_id=task_identifier)
        return HttpResponse(json.dumps({'alert': 'created', 'roi_id': job_submission.aos_id, 'id': job_submission.id, 'gt_id': generateTopo.id}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'error': str(e)}), content_type='application/json')


def startTopographyProcessing(roi_id, job_submission_id, gt_id, jsonReq, req_host):
    os.chdir(ACTUAL_PATH)  # change to this path
    worsica_logger.info('[startTopographyProcessing]: ' + os.getcwd())
    job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, aos_id=roi_id, isVisible=True)
    job_submission_exec = json.loads(job_submission.exec_arguments)

    PROVIDER = job_submission.provider
    SERVICE = job_submission.service
    USER_ID = str(job_submission.user_id)
    ROI_ID = str(job_submission.aos_id)
    SIMULATION_ID = str(job_submission.simulation_id)
    GENERATE_TOPO_ID = str(gt_id)
    generateTopo = worsica_api_models.GenerateTopographyMap.objects.get(id=gt_id)
    WATER_INDEX = str(job_submission_exec['detection']['waterIndex'])
    JOB_SUBMISSION_NAME = job_submission.name.encode('ascii', 'ignore').decode('ascii')

    WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID) + '/gt' + str(GENERATE_TOPO_ID)
    PATH_TO_PRODUCTS = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/' + WORKSPACE_SIMULATION_FOLDER
    NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER

    worsica_logger.info('[startTopographyProcessing]: ' + JOB_SUBMISSION_NAME + ' ' + SERVICE + '-user' + str(USER_ID) + '-roi' +
                        str(ROI_ID) + '-simulation' + str(SIMULATION_ID) + '-gt' + str(GENERATE_TOPO_ID) + ' -- ' + job_submission.state)
    timestamp = datetime.datetime.today().strftime('%Y%m%d-%H%M%S')  # job_submission.lastRunAt.strftime('%Y%m%d-%H%M%S') #

    LOGS_FOLDER = './log'
    LOG_FILENAME = str(timestamp) + '-worsica-processing-service-' + SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-simulation' + str(SIMULATION_ID)  # +'-gt'+str(GENERATE_TOPO_ID)

    ZREF = str(jsonReq['zref'])

    COORDS = None
    TIDE_FILE = None
    if generateTopo.generateTopographyFrom == 'probing':
        if 'coords' in jsonReq:
            pointAux = jsonReq['coords'].split(",")
            pointAux2 = [pointAux[1], pointAux[0]]
            COORDS = ", ".join(pointAux2)

    elif generateTopo.generateTopographyFrom == 'upload':
        if 'encodedFile' in jsonReq:
            tide_file_name = generateTopo.name + '_topo.txt'
            # if not os.path.exists(PATH_TO_PRODUCTS):
            print(PATH_TO_PRODUCTS)
            os.makedirs(PATH_TO_PRODUCTS, exist_ok=True)
            TIDE_FILE = PATH_TO_PRODUCTS + '/' + tide_file_name
            decodedData = b64decode(str(jsonReq['encodedFile']).encode('ascii')).decode('ascii')
            with open(TIDE_FILE, 'w') as f:
                f.write(decodedData)

    if (generateTopo.processState in ['submitted', 'error-generating-topo', 'error-storing-generating-topo']):
        PREV_JOB_SUBMISSION_STATE = job_submission.state

        try:
            print('[startTopographyProcessing]: Start generating topo...')
            task_identifier = get_task_identifier_generate_topo(job_submission, generateTopo.id)
            print(task_identifier)
            pw = 1
            print('[startTopographyProcessing]: Launch children ' + str(pw))
            _tid = task_identifier + '-generating-topo-child' + str(pw)
            tasks.taskStartTopographyGenerating.apply_async(
                args=[
                    GENERATE_TOPO_ID,
                    SERVICE,
                    USER_ID,
                    ROI_ID,
                    SIMULATION_ID,
                    PREV_JOB_SUBMISSION_STATE,
                    ZREF,
                    COORDS,
                    TIDE_FILE,
                    WATER_INDEX,
                    LOGS_FOLDER,
                    LOG_FILENAME,
                    req_host],
                task_id=task_identifier)

        except Exception as e:
            print(traceback.format_exc())


@csrf_exempt
def delete_job_submission_topography(request, job_submission_id, process_gt_id):
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        generateTopo = worsica_api_models.GenerateTopographyMap.objects.get(jobSubmission=job_submission, id=process_gt_id)
        # remove zip from nextcloud
        print('remove from nextcloud')
        SERVICE = job_submission.service
        USER_ID = job_submission.user_id
        ROI_ID = job_submission.aos_id
        SIMULATION_ID = job_submission.simulation_id
        GT_ID = generateTopo.id
        WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID) + '/gt' + str(GT_ID)
        shp_output = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-gt' + str(GT_ID) + '-' + generateTopo.name + '-final-products'
        NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER
        r = requests.delete(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + shp_output + '.zip', auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
        if (r.status_code == 204):
            worsica_logger.info('removed nextcloud ' + NEXTCLOUD_PATH_TO_PRODUCTS)
        print('delete topography')
        generateTopo.delete()
        jsonResponse = {
            "alert": 'deleted',
            "job_submission_id": job_submission_id,
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        jsonResponse = {
            "alert": 'error',
            "job_submission_id": job_submission_id,
            "response": str(e),
        }
        return HttpResponse(json.dumps(jsonResponse), content_type='application/json')


@csrf_exempt
def download_job_submission_topography(request, job_submission_id, process_gt_id):
    try:
        job_submission = worsica_api_models.JobSubmission.objects.get(id=job_submission_id, isVisible=True)
        generateTopo = worsica_api_models.GenerateTopographyMap.objects.get(jobSubmission=job_submission, id=process_gt_id)
        # remove zip from nextcloud
        print('get download from nextcloud')
        SERVICE = job_submission.service
        USER_ID = job_submission.user_id
        ROI_ID = job_submission.aos_id
        SIMULATION_ID = job_submission.simulation_id
        GT_ID = generateTopo.id
        WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID) + '/gt' + str(GT_ID)
        zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-gt' + str(GT_ID) + '-' + generateTopo.name + '-final-products'
        NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER
        return HttpResponse(json.dumps({'state': 'ok', 'url': str(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + zipFileName + '.zip'), 'appropriateFileName': zipFileName +
                                        '-final-products.zip', 'user': nextcloud_access.NEXTCLOUD_USER, 'pwd': nextcloud_access.NEXTCLOUD_PWD}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'state': 'error', 'error': 'worsica-intermediate:' + str(e)}), content_type='application/json')


#
@csrf_exempt
def list_user_repository_imageset(request, user_id, service_type):
    try:
        if (request.GET.get('roi_polygon')):
            coords = request.GET.get('roi_polygon').split('|')
            print(service_type)
            print(coords)
            uY, uX, lY, lX = coords
            roi_polygon = 'POLYGON ((' + uY + ' ' + uX + ',' + uY + ' ' + lX + ',' + lY + ' ' + lX + ',' + lY + ' ' + uX + ',' + uY + ' ' + uX + '))'

            _create_user_repository(user_id)

            export_srid = 3857
            GEOJSON_AOS_POLYGON = GEOSGeometry("SRID=4326;" + roi_polygon)
            GEOJSON_AOS_POLYGON_TRANSF = GEOJSON_AOS_POLYGON.transform(export_srid, clone=True)

            user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))

            # imagesets
            jsonImagesetsList = []
            imgsts = worsica_api_models.UserRepositoryImageSets.objects.filter(user_repository=user_repository).order_by('-issueDate')
            for img in imgsts:
                _extent = []
                _compatible = False
                img_wkt_bounds = None
                if img.wkt_bounds:
                    img_wkt_bounds = subsubtasks._normalize_wkt(img.wkt_bounds)
                    GEOJSON_GEOM_POLYGON = GEOSGeometry(img_wkt_bounds)
                    GEOJSON_GEOM_POLYGON_TRANSF = GEOJSON_GEOM_POLYGON.transform(export_srid, clone=True)
                    GEOJSON_GEOM_POLYGON_TRANSF2 = GEOJSON_GEOM_POLYGON.transform(4326, clone=True)
                    e = GEOJSON_GEOM_POLYGON_TRANSF2.extent
                    _extent = [e[1], e[0], e[3], e[2]]
                    print(GEOJSON_GEOM_POLYGON_TRANSF2.wkt)
                    _compatible = GEOJSON_AOS_POLYGON_TRANSF.intersects(GEOJSON_GEOM_POLYGON_TRANSF)
                    img_wkt_bounds2 = 'SRID=4326; ' + subsubtasks._swap_coordinates_geojson_aos_polygon(GEOJSON_GEOM_POLYGON_TRANSF2.wkt)

                iD = img.issueDate.strftime("%Y-%m-%d")

                r = {
                    'id': img.id,
                    'name': img.name,
                    'date': str(iD),
                    'state': img.state,
                    'uuid': img.uuid,
                    'provider': img.provider,
                    'extent': _extent,
                    'tilenumber': 'Drone',
                    'compatible': _compatible,
                    'bbox': img_wkt_bounds2}

                if img.image_arguments:
                    r['image_arguments'] = json.loads(img.image_arguments)  # bands
                else:
                    redband, greenband, blueband, nirband = 1, 2, 3, 4
                    r['image_arguments'] = {'redband': redband, 'greenband': greenband, 'blueband': blueband, 'nirband': nirband}
                r['image_arguments']['issueDate'] = str(iD)

                jsonImagesetsList.append(r)

            return HttpResponse(json.dumps({'alert': 'ok', 'user_id': user_id, 'imagesets': jsonImagesetsList}), content_type='application/json')
        else:
            return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'error': 'Missing roi_polygon!'}), content_type='application/json')

    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'error': str(e)}), content_type='application/json')


@csrf_exempt
def upload_user_repository_imageset(request, user_id, service_type):
    data = json.loads(request.body.decode('utf-8'))
    myfilename = data['filename']
    myfilename_noext = myfilename[:-4]
    # create user repository geometry
    worsica_logger.info('[upload_user_repository_imageset]: create user repository geometry')
    user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
    imgset, imgset_created = worsica_api_models.UserRepositoryImageSets.objects.get_or_create(
        user_repository=user_repository, name=myfilename_noext, reference=myfilename_noext,
        provider='drone')
    imgset.uploadDate = datetime.datetime.now()
    imgset.uuid = 'user' + str(user_id) + '-upload' + str(imgset.id)
    imgset.state = 'uploading'
    nc_path = nextcloud_access.NEXTCLOUD_URL_PATH + '/user_repository/user' + (user_id) + '/imagesets/' + myfilename
    imgset.filePath = nc_path
    imgset.save()

    tmppath = '/tmp/user_repository/user' + str(user_id) + '/imagesets/' + service_type
    ZIP_FILE_PATH = tmppath + '/' + myfilename_noext + '.zip'
    # upload
    fntif = None
    try:
        # create the folder (if does not exist)
        _create_user_repository(user_id)
        # store uploaded file to tmp
        worsica_logger.info('[upload_user_repository_imageset]: download file to tmp')
        auth = (nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD)
        r = requests.get(nc_path, auth=auth)
        if r.status_code == 200:
            os.makedirs(tmppath, exist_ok=True)
            with open(ZIP_FILE_PATH, 'wb') as f:
                f.write(r.content)
        zf = zipfile.ZipFile(ZIP_FILE_PATH, 'r')
        zf.extractall(tmppath + '/' + myfilename_noext)
        hasTIF = False
        for file_name in zf.namelist():
            if '__MACOSX' not in file_name:  # do not search tif under __MACOSX
                if file_name.endswith('.tif'):
                    fntif = file_name
                    hasTIF = True
        if (hasTIF):
            print(fntif)
            imgset.state = 'uploaded'
            imgset.save()
        else:
            imgset.state = 'error-uploading'
            imgset.save()
            raise Exception('Sorry, this ZIP file must have at least a tif file.')
        TIF_FILE_PATH = tmppath + '/' + myfilename_noext + '/' + fntif
        JPG_THUMBNAIL_FILE_PATH = tmppath + '/' + myfilename_noext + '/thumbnail_' + fntif.replace('.tif', '.jpg')
        print(TIF_FILE_PATH)
        print(JPG_THUMBNAIL_FILE_PATH)
        # do the validation file checks
        # check if has tif is geotiff and has binary values
        worsica_logger.info('[upload_user_repository_imageset]: open tif file')
        worsica_logger.info('[upload_user_repository_imageset]: check if it is a geotiff file')
        rst = GDALRaster(TIF_FILE_PATH, write=False)
        if rst:
            rstband = rst.bands
            if (len(rstband) < 4):
                imgset.state = 'error-uploading'
                imgset.save()
                raise Exception('This image does not have at least 4 bands')
            else:
                # store wkt_bounds
                (x1, y1, x2, y2) = rst.extent
                polygon = "POLYGON ((" + str(x1) + " " + str(y1) + ", " + str(x1) + " " + str(y2) + ", " + str(x2) + \
                    " " + str(y2) + ", " + str(x2) + " " + str(y1) + ", " + str(x1) + " " + str(y1) + "))"
                srid = rst.srs.srid
                GEOJSON_MASK_POLYGON = GEOSGeometry("SRID=" + str(srid) + ";" + polygon)
                imgset.wkt_bounds = str(GEOJSON_MASK_POLYGON)
                imgset.save()
                # gdal_translate rgba.tif rgba_thumbnail.jpg -outsize 512 512 -of JPEG -scale
                gdal.Translate(JPG_THUMBNAIL_FILE_PATH, TIF_FILE_PATH, format='JPEG', width=512, height=512, scaleParams=[[]])
                if os.path.exists(JPG_THUMBNAIL_FILE_PATH):
                    print('Thumbnail created, upload it!')
                    imgset.thumbnailImage = open(JPG_THUMBNAIL_FILE_PATH, 'rb').read()
                    imgset.save()
                    os.remove(JPG_THUMBNAIL_FILE_PATH)
                os.remove(TIF_FILE_PATH)
                return HttpResponse(json.dumps({'alert': 'uploaded', 'user_id': user_id, 'imgset_id': imgset.id, }), content_type='application/json')
        else:
            imgset.state = 'error-uploading'
            imgset.save()
            raise Exception('Not a valid geotiff file image!')

    except Exception as e:
        imgset_id = imgset.id
        imgset.delete()
        # delete the invalid file
        worsica_logger.info('[upload_user_repository_imageset]: invalid file!')
        r = requests.delete(nc_path, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
        if (r.status_code == 204):
            worsica_logger.info('[upload_user_repository_imageset]: remove file ' + nc_path)
            os.remove(ZIP_FILE_PATH)
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'imgset_id': imgset_id, 'message': str(e)}), content_type='application/json')


@csrf_exempt
def show_user_repository_imageset(request, user_id, service_type, imageset_id):
    try:
        user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
        imgset = worsica_api_models.UserRepositoryImageSets.objects.get(user_repository=user_repository, id=imageset_id)
        response = {'alert': 'success', 'user_id': user_id, 'imgset_id': imageset_id, }
        iD = imgset.issueDate.strftime("%Y-%m-%d")
        response['date'] = str(iD)
        if imgset.image_arguments:
            response['image_arguments'] = json.loads(imgset.image_arguments)  # bands
        else:
            redband, greenband, blueband, nirband = 1, 2, 3, 4
            response['image_arguments'] = {'redband': redband, 'greenband': greenband, 'blueband': blueband, 'nirband': nirband}
        response['image_arguments']['issueDate'] = str(iD)
        return HttpResponse(json.dumps(response), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'imgset_id': imageset_id, 'message': str(e)}), content_type='application/json')


@csrf_exempt
def show_thumbnail_user_repository_imageset(request, user_id, service_type, imageset_id):
    try:
        user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
        imgset = worsica_api_models.UserRepositoryImageSets.objects.get(user_repository=user_repository, id=imageset_id)
        if imgset.thumbnailImage:
            encodedImage = b64encode(imgset.thumbnailImage).decode('ascii')
            return HttpResponse(json.dumps({'alert': 'success', 'user_id': user_id, 'imgset_id': imageset_id, 'uuid': imgset.uuid, 'encodedImage': encodedImage}), content_type='application/json')
        else:
            raise Exception('Exception: No thumbnail found for imageset ' + str(imageset_id))
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'imgset_id': imageset_id, 'message': str(e)}), content_type='application/json')


@csrf_exempt
def edit_user_repository_imageset(request, user_id, service_type, imageset_id):
    data = json.loads(request.body.decode('utf-8'))
    try:
        user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
        imgset = worsica_api_models.UserRepositoryImageSets.objects.get(user_repository=user_repository, id=imageset_id)
        imgset.image_arguments = json.dumps(data['image_arguments'])  # bands
        imgset.issueDate = data['issueDate']
        imgset.save()
        return HttpResponse(json.dumps({'alert': 'success', 'user_id': user_id, 'imgset_id': imageset_id, }), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'imgset_id': imageset_id, 'message': str(e)}), content_type='application/json')

#


@csrf_exempt
def delete_user_repository_imageset(request, user_id, service_type, imageset_id):
    try:
        user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
        imgset = worsica_api_models.UserRepositoryImageSets.objects.get(user_repository=user_repository, id=int(imageset_id))
        # delete file
        auth = (nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD)
        path = nextcloud_access.NEXTCLOUD_URL_PATH + '/user_repository/user' + str(user_id) + '/imagesets/' + imgset.name + '.zip'
        r = requests.delete(path, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
        if (r.status_code == 204):
            worsica_logger.info('[delete_simulation]: remove file ' + path)
        imgset.delete()
        return HttpResponse(json.dumps({'alert': 'deleted', 'user_id': user_id, 'imgset_id': imageset_id}), content_type='application/json')
    except Exception as e:
        print(traceback.format_exc())
        return HttpResponse(json.dumps({'alert': 'error', 'user_id': user_id, 'imgset_id': imageset_id,
                                        'error': 'Something went wrong, try again!', 'details': str(e)}), content_type='application/json')
