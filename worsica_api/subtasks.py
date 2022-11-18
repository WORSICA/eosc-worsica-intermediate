from django.core.files import File

from django.contrib.gis.utils import LayerMapping

from django.db.models.signals import pre_save
from django.db import transaction

import worsica_api.models as worsica_api_models
import raster.models as raster_models
import json

import datetime
import time

import os
import signal

import subprocess
import requests
import zipfile

import traceback
from django.utils.text import slugify

from worsica_web_intermediate import settings, nextcloud_access
from . import logger, views, subsubtasks, utils
from osgeo import gdal
import numpy as np
import math

worsica_logger = logger.init_logger('WORSICA-Intermediate.Views', settings.LOG_PATH)
ACTUAL_PATH = os.getcwd()


def _download_imageset(PIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_USERCHOSEN_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, ESA_USER_ID, IS_USERCHOSEN_IMAGE, req_host):
    try:
        pis = (worsica_api_models.UserChosenProcessImageSet.objects.get(id=PIS_ID) if IS_USERCHOSEN_IMAGE else worsica_api_models.ProcessImageSet.objects.get(id=PIS_ID))
        dis = worsica_api_models.RepositoryImageSets.objects.get(id=pis.imageSet.id)
        try:
            UUID = pis.uuid
            IMAGESET_NAME = pis.name
            ACTUAL_PIS_ID = pis.id
            print('pis.imageSet.state=' + pis.imageSet.state)
            print('PREV_JOB_SUBMISSION_STATE=' + PREV_JOB_SUBMISSION_STATE)
            # masters
            if (pis.imageSet.state in ['submitted', 'error-downloading', 'error-download-corrupt',
                                       'download-timeout-retry', 'expired']):  # for download, it's more trustable to use this than processstate
                attempt, max_attempt = 1, 10
                cmd_timeout = 60 * 60 * 24 * 4 if settings.ENABLE_GRID else 15 * 60  # minutes to avoid download script being stuck
                leaveWhileLoop = False  # isdownloaded
                PROVIDER = 'gcp'
                while attempt <= max_attempt and not leaveWhileLoop:
                    try:
                        print('[startProcessing downloading]: attempt ' + str(attempt))
                        print('[startProcessing downloading]: refresh pis')
                        pis.refresh_from_db()
                        print('pis.imageSet.state=' + pis.imageSet.state)
                        LAST_PIS_STATE = pis.imageSet.state
                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'downloading', dis)
                        LOG_FILE = LOG_FILENAME + '-' + str(ACTUAL_PIS_ID) + '-downloading' + '-' + PROVIDER + '-' + str(attempt) + '.out'
                        COMMAND_SHELL = utils._build_command_shell_to_run(
                            './worsica_downloading_service' + ('_uc' if IS_USERCHOSEN_IMAGE else '') + '_v2.sh ' + SERVICE + ' ' + USER_ID + ' ' + ROI_ID + ' ' + SIMULATION_USERCHOSEN_ID +
                            ' ' + PREV_JOB_SUBMISSION_STATE + ' ' + UUID + ' ' + IMAGESET_NAME + ' ' + str(ESA_USER_ID) + ' ' + LAST_PIS_STATE + ' ' + PROVIDER,
                            LOGS_FOLDER + '/' + LOG_FILE,
                            settings.ENABLE_GRID)
                        worsica_logger.info('[startProcessing downloading]: command_shell=' + str(COMMAND_SHELL))
                        cmd = subprocess.Popen(COMMAND_SHELL, cwd=settings.WORSICA_FOLDER_PATH + '/worsica_web_products/', shell=False, preexec_fn=os.setsid)
                        cmd_wait = cmd.wait(timeout=cmd_timeout)
                        print('[debug cmd_wait]: ' + str(cmd_wait))
                        outFile = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/log/' + LOG_FILE
                        # get the correct states from the python script outputs by waiting...s
                        time.sleep(15)
                        # while download has not started
                        while (not views._find_word_in_output_file("Available! Start the download", outFile)):  # check state if not start download
                            print('[startProcessing downloading]: download has not started...')
                            cooldownlta = 5
                            cmd = subprocess.Popen(COMMAND_SHELL, cwd=settings.WORSICA_FOLDER_PATH + '/worsica_web_products/', shell=False, preexec_fn=os.setsid)
                            cmd_wait = cmd.wait(timeout=cmd_timeout)
                            print('[debug cmd_wait]: ' + str(cmd_wait))
                            # ESA ONLY
                            # if is waiting for LTA
                            if cmd_wait == 0 and views._find_word_in_output_file("[downloadUUID] state: download-waiting-lta", outFile):  # is lta? then update state to lta
                                attempt = attempt + 1
                                if (attempt > max_attempt):  # if max atempts exceeded
                                    print('[startProcessing downloading]: max attempts reached...')
                                    print('[startProcessing downloading]: ... mark download as error')
                                    leaveWhileLoop = True
                                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'error-downloading', dis)
                                    break  # leave loop
                                else:
                                    worsica_logger.info('[startProcessing downloading]: download-waiting-lta ' + IMAGESET_NAME + ', bash script exited successfully, but will try again some time later')
                                    # if has attempts, and is lta, go download directly to google as workaround...
                                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'download-timeout-retry', dis)
                                    break
                            # ESA ONLY
                            # if has found error-downloading-esa state, and it determined the file is corrupted on checksum, try gcp
                            # this happens if esa servers are slow to download and rarely run the download for the second time and fail.
                            elif views._find_word_in_output_file("[downloadUUID] state: error-download-corrupt", outFile) and views._find_word_in_output_file("state: error-downloading-esa", outFile):
                                views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'download-timeout-retry', dis)
                                break
                            # if has found error-downloading state, do nothing, jump out of the loop
                            elif views._find_word_in_output_file("state: error-downloading-", outFile):  # find either error-downloading-esa or error-downloading-gcp suffixes?
                                break
                            # none of these above? continue looping
                            print('[startProcessing downloading]: Hasnt started download yet, wait ' + str(cooldownlta) + ' minutes.')
                            time.sleep(cooldownlta * 60)
                        # if has downloaded
                        if cmd_wait == 0 and views._find_word_in_output_file("[downloadUUID_checkFile] state: downloaded", outFile):
                            # mark as downloaded and leave loop
                            worsica_logger.info('[startProcessing downloading]: downloaded ' + IMAGESET_NAME + ', bash script exited successfully')
                            leaveWhileLoop = True
                            views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'downloaded', dis)
                        else:
                            # or if is download corrupt
                            if views._find_word_in_output_file("[downloadUUID_checkFile] state: error-download-corrupt", outFile):  # is corrupt
                                worsica_logger.info('[startProcessing downloading]: error-download-corrupt ' + IMAGESET_NAME + ', file is corrupt')
                                attempt = attempt + 1
                                if (attempt > max_attempt):  # and if attempts exceeded
                                    worsica_logger.info('[startProcessing downloading]: ...give up, attempts exceeded...')
                                    if (PROVIDER == 'gcp'):  # on gcp, try with esa
                                        PROVIDER = 'esa'
                                        print('[startProcessing downloading]: try with ' + PROVIDER)
                                        attempt = 1
                                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'download-timeout-retry', dis)
                                        time.sleep(60)
                                    else:  # even on esa, give up
                                        print('[startProcessing downloading]: ... mark download as error')
                                        leaveWhileLoop = True
                                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'error-downloading', dis)
                                else:  # if has attempts, mark it corrupt, and try with esa
                                    worsica_logger.info('[startProcessing downloading]: ...mark as error download corrupt...')
                                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'error-download-corrupt', dis)
                                    if (PROVIDER == 'gcp'):
                                        PROVIDER = 'esa'
                                        print('[startProcessing downloading]: try with ' + PROVIDER)
                                        attempt = 1
                                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'download-timeout-retry', dis)
                                        time.sleep(60)
                            else:  # if other error
                                worsica_logger.info('[startProcessing downloading]: error on trying to download ' + IMAGESET_NAME + '...')
                                attempt = attempt + 1
                                if (attempt > max_attempt):  # and if attempts exceeded
                                    worsica_logger.info('[startProcessing downloading]: ...give up, attempts exceeded...')
                                    if (PROVIDER == 'gcp'):  # on gcp, try with esa
                                        PROVIDER = 'esa'
                                        print('[startProcessing downloading]: try with ' + PROVIDER)
                                        attempt = 1
                                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'download-timeout-retry', dis)
                                        time.sleep(60)
                                    else:  # even on esa, give up
                                        print('[startProcessing downloading]: ... mark download as error')
                                        leaveWhileLoop = True
                                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'error-downloading', dis)
                                else:  # if has attempts, mark it to retry
                                    worsica_logger.info('[startProcessing downloading]: ... retry')
                                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'download-timeout-retry', dis)
                                    time.sleep(60)  # 1 minute before retrying
                    # if an exception such as timeouts occurs
                    except Exception as e:
                        print('[startProcessing downloading]: command_shell throwed an exception ' + str(e))
                        os.killpg(os.getpgid(cmd.pid), signal.SIGINT)
                        print('[startProcessing downloading]: interrupt pid ' + str(cmd.pid))
                        os.killpg(os.getpgid(cmd.pid), signal.SIGTERM)
                        print('[startProcessing downloading]: terminated pid ' + str(cmd.pid))
                        cmd = None
                        attempt = attempt + 1
                        if (attempt > max_attempt):  # if attempts exceeded
                            print('[startProcessing downloading]: max attempts reached...')
                            if (PROVIDER == 'gcp'):  # on gcp, change to esa, try again
                                PROVIDER = 'esa'
                                print('[startProcessing downloading]: try with ' + PROVIDER)
                                attempt = 1
                                views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'download-timeout-retry', dis)
                                time.sleep(60)
                            else:  # even on esa, leave
                                print('[startProcessing downloading]: ... mark download as error')
                                leaveWhileLoop = True
                                views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'error-downloading', dis)
                        else:  # if has attempts, try again in the same provider
                            print('[startProcessing downloading]: trying again in 60s..')
                            views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'download-timeout-retry', dis)
                            time.sleep(60)  # 1 minute before trying

            else:
                # meanwhile, while others are trying to download this image, must wait
                # just 'follow' the repository image state
                while (pis.imageSet.state in ['downloading', 'download-waiting-lta', 'download-timeout-retry']):
                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, pis.imageSet.state)  # get the actual repositoryimage state and change it to pis
                    worsica_logger.info('[startProcessing downloading]: imageset ' + IMAGESET_NAME + ' downloading by another process, we need to wait 10 secs')
                    pis = (worsica_api_models.UserChosenProcessImageSet.objects.get(id=pis.id)
                           if IS_USERCHOSEN_IMAGE else worsica_api_models.ProcessImageSet.objects.get(id=pis.id))  # update dis to get the actual state of the object
                    time.sleep(10)  # wait 10 secs
                if (pis.imageSet.state in ['downloaded']):
                    print('[startProcessing downloading]: imageset downloaded')
                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, pis.imageSet.state)  # get the actual repositoryimage state and change it to pis
                else:
                    worsica_logger.info('[startProcessing downloading]: error on trying to download ' + IMAGESET_NAME +
                                        ', seems the bash script exited with fail from the side who has requested download')
                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, pis.imageSet.state)
        except Exception as e:
            print(traceback.format_exc())
            worsica_logger.info('[startProcessing downloading]: error on trying to download ' + IMAGESET_NAME + ': ' + str(e))
            views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'error-downloading', dis)
    except Exception as e:
        print(traceback.format_exc())
        worsica_logger.info('[startProcessing downloading]: ProcessImageSet ' + str(PIS_ID) + ': ' + str(e))


def _convert_l2a_imageset(PIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_USERCHOSEN_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, IS_USERCHOSEN_IMAGE, req_host):
    try:
        pis = (worsica_api_models.UserChosenProcessImageSet.objects.get(id=PIS_ID) if IS_USERCHOSEN_IMAGE else worsica_api_models.ProcessImageSet.objects.get(id=PIS_ID))
        if pis.processState in ['downloaded', 'error-converting']:
            dis = worsica_api_models.RepositoryImageSets.objects.get(id=pis.imageSet.id).childRepositoryImageSet
            # if an image is required to be converted to l2a, but a l2a was previously download, skip this step
            # key difference is on its state: downloaded and submitted
            if (pis.convertToL2A and dis.state in ['downloaded']):
                worsica_logger.info('[startProcessing converting]: WARNING: ' + dis.name + ' already exists on our repository (was previously downloaded), skip conversion.')
            else:  # pis.convertToL2A and dis.state in ['submitted','error-converting']
                IMAGESET_NAME = pis.name
                NEW_IMAGESET_NAME = IMAGESET_NAME.replace('L1C', 'L2A')
                SIMULATION_NAME = IMAGESET_NAME
                ACTUAL_PIS_ID = pis.id
                try:
                    worsica_logger.info('[startProcessing converting]: found l2a: ' + dis.name)
                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'converting', dis)
                    LOG_FILE = LOG_FILENAME + '-' + str(ACTUAL_PIS_ID) + '-converting.out'
                    COMMAND_SHELL = utils._build_command_shell_to_run(
                        './worsica_atmospheric_correction_service' + ('_uc' if IS_USERCHOSEN_IMAGE else '') + '.sh ' + SERVICE + ' ' + USER_ID + ' ' + ROI_ID + ' ' +
                        SIMULATION_USERCHOSEN_ID + ' ' + PREV_JOB_SUBMISSION_STATE + ' ' + SIMULATION_NAME + ' ' + IMAGESET_NAME + ' ' + NEW_IMAGESET_NAME,
                        LOGS_FOLDER + '/' + LOG_FILE,
                        settings.ENABLE_GRID)
                    cmd_timeout = 60 * 60 * 24 * 4 if settings.ENABLE_GRID else 60 * 60  # minutes to avoid download script being stuck
                    worsica_logger.info('[startProcessing converting]: command_shell=' + str(COMMAND_SHELL))
                    cmd2 = subprocess.Popen(COMMAND_SHELL, cwd=settings.WORSICA_FOLDER_PATH + '/worsica_web_products/', shell=False, preexec_fn=os.setsid)
                    cmd2_wait = cmd2.wait(timeout=cmd_timeout)
                    print('[debug cmd2_wait]: ' + str(cmd2_wait))
                    out2File = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/log/' + LOG_FILE
                    if cmd2_wait == 0 and views._find_word_in_output_file("state: converted", out2File):
                        print('[startProcessing converting]: imageset ' + IMAGESET_NAME + ' converted')
                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'converted', dis)
                    else:
                        worsica_logger.info('[startProcessing converting]: error on trying to convert ' + IMAGESET_NAME + ', bash script exited with fail')
                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'error-converting', dis)
                except Exception as e:
                    worsica_logger.info('[startProcessing converting]: error on trying to convert ' + IMAGESET_NAME + ': ' + str(e))
                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'error-converting', dis)
        else:
            worsica_logger.info('[startProcessing converting]: ProcessImageSet ' + str(PIS_ID) + ' state is not state downloaded or error-converting. Skip.')
    except Exception as e:
        worsica_logger.info('[startProcessing converting]: ProcessImageSet ' + str(PIS_ID) + ' not found: ' + str(e))


def _resample_imageset(PIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_USERCHOSEN_ID, PREV_JOB_SUBMISSION_STATE, GEOJSON_POLYGON, LOGS_FOLDER, LOG_FILENAME, IS_USERCHOSEN_IMAGE, req_host):
    try:
        pis = (worsica_api_models.UserChosenProcessImageSet.objects.get(id=PIS_ID) if IS_USERCHOSEN_IMAGE else worsica_api_models.ProcessImageSet.objects.get(id=PIS_ID))
        if pis.processState in ['downloaded', 'uploaded', 'error-resampling', 'converted']:
            IMAGESET_NAME = pis.name
            if pis.convertToL2A:
                IMAGESET_NAME = pis.name.replace('L1C', 'L2A')
            SIMULATION_NAME = IMAGESET_NAME
            ACTUAL_PIS_ID = pis.id
            BANDS = '\"auto\"'
            _script_command_prefix = ('_uc' if IS_USERCHOSEN_IMAGE else '')  # if empty, run resampling script normally, if user chosen image, run UC
            if 'uploaded-user' in pis.name and pis.uploadedimageSet is not None:
                print('[startProcessing resampling]: uploaded imageset')
                image_arguments = json.loads(pis.uploadedimageSet.image_arguments)
                print(image_arguments)
                BANDS = '\"' + ';'.join([str(k) + '=' + str(v) for k, v in image_arguments.items() if k != 'issueDate']) + '\"'
                _script_command_prefix = '_upload'  # ('_upload' if IS_USERCHOSEN_IMAGE else '')
            print(BANDS)
            try:
                views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'resampling')
                LOG_FILE = LOG_FILENAME + '-' + str(ACTUAL_PIS_ID) + '-resampling.out'  # ('-gcp' if LAST_PIS_STATE in ['error-download-corrupt','download-timeout-retry'] else '')+'.out'
                COMMAND_SHELL = utils._build_command_shell_to_run(
                    './worsica_resampling_service' + _script_command_prefix + '_v2.sh ' + SERVICE + ' ' + USER_ID + ' ' + ROI_ID + ' ' + SIMULATION_USERCHOSEN_ID +
                    ' ' + PREV_JOB_SUBMISSION_STATE + ' ' + IMAGESET_NAME + ' ' + SIMULATION_NAME + ' ' + GEOJSON_POLYGON + ' ' + BANDS,
                    LOGS_FOLDER + '/' + LOG_FILE,
                    settings.ENABLE_GRID)
                cmd_timeout = 60 * 60 * 24 * 4 if settings.ENABLE_GRID else 15 * 60  # minutes to avoid download script being stuck
                worsica_logger.info('[startProcessing resampling]: command_shell=' + str(COMMAND_SHELL))
                cmd2 = subprocess.Popen(COMMAND_SHELL, cwd=settings.WORSICA_FOLDER_PATH + '/worsica_web_products/', shell=False)
                cmd2_wait = cmd2.wait(timeout=cmd_timeout)
                print('[debug cmd2_wait]: ' + str(cmd2_wait))
                out2File = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/log/' + LOG_FILE
                if cmd2_wait == 0 and views._find_word_in_output_file("state: resampled", out2File):
                    print('[startProcessing resampling]: imageset ' + IMAGESET_NAME + ' resampled')
                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'resampled')
                elif views._find_word_in_output_file("Error on unzip! Corrupt file!!", out2File):
                    worsica_logger.info('[startProcessing resampling]: error-download-corrupt ' + IMAGESET_NAME + ', file is corrupt. set error-resampling')
                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'error-resampling')
                else:
                    worsica_logger.info('[startProcessing resampling]: error on trying to resample ' + IMAGESET_NAME + ', bash script exited with fail')
                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'error-resampling')
            except Exception as e:
                worsica_logger.info('[startProcessing resampling]: error on trying to resample ' + IMAGESET_NAME + ': ' + str(e))
                views._change_imageset_state(IS_USERCHOSEN_IMAGE, pis, 'error-resampling')
        else:
            worsica_logger.info('[startProcessing resampling]: ProcessImageSet ' + str(PIS_ID) + ' state is not state downloaded, converted or error-resampling. Skip.')
    except Exception as e:
        worsica_logger.info('[startProcessing resampling]: ProcessImageSet ' + str(PIS_ID) + ' not found: ' + str(e))


def _merge_imagesets(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_USERCHOSEN_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, IS_USERCHOSEN_IMAGE, req_host):
    try:
        mpis = (worsica_api_models.UserChosenMergedProcessImageSet.objects.get(id=MPIS_ID) if IS_USERCHOSEN_IMAGE else worsica_api_models.MergedProcessImageSet.objects.get(id=MPIS_ID))
        if mpis.processState in ['submitted', 'error-merging']:
            # TODO: do not use error-download-corrupt
            print(mpis.procImageSet_ids)
            print(str(IS_USERCHOSEN_IMAGE))
            time.sleep(10)
            pis_objs = worsica_api_models.UserChosenProcessImageSet.objects if IS_USERCHOSEN_IMAGE else worsica_api_models.ProcessImageSet.objects
            _lst_imgsets = []
            for i in mpis.procImageSet_ids.split(','):
                if pis_objs.get(id=int(i)).processState in ['resampled']:
                    pis = pis_objs.get(id=int(i))
                    _lst_imgsets.append(pis.name.replace('L1C', 'L2A') if pis.convertToL2A else pis.name)
            IMAGESETS_NAME = ','.join(_lst_imgsets)
            MERGED_IMAGE_NAME = mpis.name
            try:
                views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'merging')
                LOG_FILE = LOG_FILENAME + '-' + str(mpis.id) + '-merging.out'
                COMMAND_SHELL = utils._build_command_shell_to_run(
                    './worsica_merging_service' + ('_uc' if IS_USERCHOSEN_IMAGE else '') + '_v2.sh ' + SERVICE + ' ' + USER_ID + ' ' + ROI_ID + ' ' +
                    SIMULATION_USERCHOSEN_ID + ' ' + PREV_JOB_SUBMISSION_STATE + ' ' + IMAGESETS_NAME + ' ' + MERGED_IMAGE_NAME,
                    LOGS_FOLDER + '/' + LOG_FILE,
                    settings.ENABLE_GRID)
                cmd_timeout = 60 * 60 * 24 * 4 if settings.ENABLE_GRID else 15 * 60  # minutes to avoid download script being stuck
                worsica_logger.info('[startProcessing merging]: command_shell=' + str(COMMAND_SHELL))
                cmd2 = subprocess.Popen(COMMAND_SHELL, cwd=settings.WORSICA_FOLDER_PATH + '/worsica_web_products/', shell=False)
                cmd2_wait = cmd2.wait(timeout=cmd_timeout)
                print('[debug cmd2_wait]: ' + str(cmd2_wait))
                out2File = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/log/' + LOG_FILE
                if cmd2_wait == 0 and views._find_word_in_output_file("state: merged", out2File):
                    print('[startProcessing merging]: imageset ' + MERGED_IMAGE_NAME + ' merged')
                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'merged')
                else:
                    worsica_logger.info('[startProcessing merging]: error on trying to merge ' + MERGED_IMAGE_NAME + ', bash script exited with fail')
                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'error-merging')
            except Exception as e:
                worsica_logger.info('[startProcessing merging]: error on trying to merge ' + MERGED_IMAGE_NAME + ': ' + str(e))
                views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'error-merging')
        else:
            worsica_logger.info('[startProcessing merging]: MergedProcessImageSet ' + str(MPIS_ID) + ' state is not submitted or error-merging. Skip.')
    except Exception as e:
        worsica_logger.info('[startProcessing merging]: MergedProcessImageSet ' + str(MPIS_ID) + ' not found: ' + str(e))

# ----------water leak ph1------------


def _generate_virtual_mergedimageset(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, SAMPLE_IMAGESET_NAME, SIMULATION_NAME2, req_host):
    if SERVICE == 'waterleak':
        try:
            mpis = worsica_api_models.MergedProcessImageSet.objects.get(id=MPIS_ID, isVirtual=True)
            if mpis.processState in ['submitted', 'error-generating-virtual']:
                IMAGESET_NAME = mpis.name
                SIMULATION_NAME = mpis.name
                try:
                    views._change_imageset_state(False, mpis, 'generating-virtual')
                    LOG_FILE = LOG_FILENAME + '-' + str(mpis.id) + '-generating-virtual.out'
                    COMMAND_SHELL = utils._build_command_shell_to_run(
                        './worsica_generating_virtual_imgs_service_v2.sh ' + SERVICE + ' ' + USER_ID + ' ' + ROI_ID + ' ' + SIMULATION_ID + ' ' +
                        PREV_JOB_SUBMISSION_STATE + ' ' + IMAGESET_NAME + ' ' + SIMULATION_NAME + ' ' + SAMPLE_IMAGESET_NAME + ' ' + SIMULATION_NAME2,
                        LOGS_FOLDER + '/' + LOG_FILE,
                        settings.ENABLE_GRID)
                    cmd_timeout = 60 * 60 * 24 * 4 if settings.ENABLE_GRID else 15 * 60  # minutes to avoid download script being stuck
                    worsica_logger.info('[startProcessing generating-virtual]: command_shell=' + str(COMMAND_SHELL))
                    cmd2 = subprocess.Popen(COMMAND_SHELL, cwd=settings.WORSICA_FOLDER_PATH + '/worsica_web_products/', shell=False)
                    cmd2_wait = cmd2.wait(timeout=cmd_timeout)
                    print('[debug cmd2_wait]: ' + str(cmd2_wait))
                    out2File = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/log/' + LOG_FILE
                    if cmd2_wait == 0 and views._find_word_in_output_file("state: generated-virtual", out2File):  # and (b"state: processed" in out2):
                        print('[startProcessing generating-virtual]: imageset ' + IMAGESET_NAME + ' generated-virtual')
                        views._change_imageset_state(False, mpis, 'generated-virtual')
                    else:
                        worsica_logger.info('[startProcessing generating-virtual]: error on trying to generate virtual images ' + IMAGESET_NAME + ', bash script exited with fail')
                        views._change_imageset_state(False, mpis, 'error-generating-virtual')
                except Exception as e:
                    worsica_logger.info('[startProcessing generating-virtual]: error on trying to generate virtual images ' + IMAGESET_NAME + ': ' + str(e))
                    views._change_imageset_state(False, mpis, 'error-generating-virtual')
            else:
                worsica_logger.info('[startProcessing generating-virtual]: MergedProcessImageSet ' + str(MPIS_ID) + ' state is not merged or error-generating-virtual. Skip.')
        except Exception as e:
            worsica_logger.info('[startProcessing generating-virtual]: MergedProcessImageSet ' + str(MPIS_ID) + ' not found: ' + str(e))
    else:
        worsica_logger.info('[startProcessing generating-virtual]: Generation of virtual images only available on waterleak service. Skip.')


def _interpolation_mergedimageset(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, req_host):
    if SERVICE == 'waterleak':
        try:
            mpis = worsica_api_models.MergedProcessImageSet.objects.get(id=MPIS_ID)
            if mpis.processState in ['merged', 'generated-virtual', 'error-interpolating-products']:
                # if has ids to interpolate, do interpolation
                if mpis.interpolationImageSet_ids:
                    # do the interpolation if the images are either merged, generated or already interpolated (this is a fix to avoid possible crashes)
                    IMAGESETS_NAME = ','.join([worsica_api_models.MergedProcessImageSet.objects.get(id=int(i)).name for i in mpis.interpolationImageSet_ids.split(
                        ',') if worsica_api_models.MergedProcessImageSet.objects.get(id=int(i)).processState in ['merged', 'generated-virtual', 'interpolated-products']])
                    IMAGESETS_DATE = ','.join([worsica_api_models.MergedProcessImageSet.objects.get(id=int(i)).sensingDate.strftime("%Y-%m-%d") for i in mpis.interpolationImageSet_ids.split(',')
                                              if worsica_api_models.MergedProcessImageSet.objects.get(id=int(i)).processState in ['merged', 'generated-virtual', 'interpolated-products']])
                    MERGED_IMAGE_NAME = mpis.name
                    MERGED_IMAGE_DATE = mpis.sensingDate.strftime("%Y-%m-%d")
                    try:
                        views._change_imageset_state(False, mpis, 'interpolating-products')
                        LOG_FILE = LOG_FILENAME + '-' + str(mpis.id) + '-interpolating-products.out'
                        COMMAND_SHELL = utils._build_command_shell_to_run(
                            './worsica_interpolating_products_service_v2.sh ' + SERVICE + ' ' + USER_ID + ' ' + ROI_ID + ' ' + SIMULATION_ID + ' ' +
                            PREV_JOB_SUBMISSION_STATE + ' ' + MERGED_IMAGE_NAME + ' ' + MERGED_IMAGE_DATE + ' ' + IMAGESETS_NAME + ' ' + IMAGESETS_DATE,
                            LOGS_FOLDER + '/' + LOG_FILE,
                            settings.ENABLE_GRID)
                        cmd_timeout = 60 * 60 * 24 * 4 if settings.ENABLE_GRID else 15 * 60  # minutes to avoid download script being stuck
                        worsica_logger.info('[startProcessing interpolating-products]: command_shell=' + str(COMMAND_SHELL))
                        cmd2 = subprocess.Popen(COMMAND_SHELL, cwd=settings.WORSICA_FOLDER_PATH + '/worsica_web_products/', shell=False)
                        cmd2_wait = cmd2.wait(timeout=cmd_timeout)
                        print('[debug cmd2_wait]: ' + str(cmd2_wait))
                        out2File = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/log/' + LOG_FILE
                        if cmd2_wait == 0 and views._find_word_in_output_file("state: interpolated-products", out2File):
                            print('[startProcessing interpolating-products]: imageset ' + MERGED_IMAGE_NAME + ' interpolated')
                            views._change_imageset_state(False, mpis, 'interpolated-products')
                        else:
                            worsica_logger.info('[startProcessing interpolating-products]: error on trying to interpolate products ' + MERGED_IMAGE_NAME + ', bash script exited with fail')
                            views._change_imageset_state(False, mpis, 'error-interpolating-products')
                    except Exception as e:
                        worsica_logger.info('[startProcessing interpolating-products]: error on trying to minterpolate products ' + MERGED_IMAGE_NAME + ': ' + str(e))
                        views._change_imageset_state(False, mpis, 'error-interpolating-products')
                else:
                    worsica_logger.info('[startProcessing interpolating-products]: no ids to interpolate (may be a first or last image), skip')
                    views._change_imageset_state(False, mpis, 'interpolated-products')
            else:
                worsica_logger.info('[startProcessing interpolating-products]: MergedProcessImageSet ' + str(MPIS_ID) + ' state is not generated-virtual or error-interpolating-products. Skip.')
        except Exception as e:
            worsica_logger.info('[startProcessing interpolating-products]: MergedProcessImageSet ' + str(MPIS_ID) + ' not found: ' + str(e))
    else:
        worsica_logger.info('[startProcessing interpolating-products]: Interpolation of images only available on waterleak service. Skip.')
# --------------------------------


def _process_mergedimageset(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_USERCHOSEN_ID, PREV_JOB_SUBMISSION_STATE, BATH_VALUE,
                            TOPO_VALUE, WI_THRESHOLD, WATER_INDEX, LOGS_FOLDER, LOG_FILENAME, IS_USERCHOSEN_IMAGE, req_host):
    try:
        mpis = (worsica_api_models.UserChosenMergedProcessImageSet.objects.get(id=MPIS_ID) if IS_USERCHOSEN_IMAGE else worsica_api_models.MergedProcessImageSet.objects.get(id=MPIS_ID))
        if mpis.processState in ['merged', 'stored-rgb', 'interpolated-products', 'error-processing']:
            IMAGESET_NAME = mpis.name
            SIMULATION_NAME = mpis.name
            try:
                views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'processing')
                LOG_FILE = LOG_FILENAME + '-' + str(mpis.id) + '-processing.out'
                COMMAND_SHELL = utils._build_command_shell_to_run(
                    './worsica_processing_service' + ('_uc' if IS_USERCHOSEN_IMAGE else '') + '_v2.sh ' + SERVICE + ' ' + USER_ID + ' ' + ROI_ID + ' ' + SIMULATION_USERCHOSEN_ID + ' ' +
                    PREV_JOB_SUBMISSION_STATE + ' ' + IMAGESET_NAME + ' ' + SIMULATION_NAME + ' ' + BATH_VALUE + ' ' + TOPO_VALUE + ' ' + WI_THRESHOLD + ' ' + WATER_INDEX,
                    LOGS_FOLDER + '/' + LOG_FILE,
                    settings.ENABLE_GRID)
                cmd_timeout = 60 * 60 * 24 * 4 if settings.ENABLE_GRID else 15 * 60  # minutes to avoid download script being stuck
                worsica_logger.info('[startProcessing processing]: command_shell=' + str(COMMAND_SHELL))
                cmd2 = subprocess.Popen(COMMAND_SHELL, cwd=settings.WORSICA_FOLDER_PATH + '/worsica_web_products/', shell=False)
                cmd2_wait = cmd2.wait(timeout=cmd_timeout)
                print('[debug cmd2_wait]: ' + str(cmd2_wait))
                out2File = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/log/' + LOG_FILE
                if cmd2_wait == 0 and views._find_word_in_output_file("state: processed", out2File):  # and (b"state: processed" in out2):
                    print('[startProcessing processing]: imageset ' + IMAGESET_NAME + ' processed')
                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'processed')
                else:
                    worsica_logger.info('[startProcessing processing]: error on trying to process ' + IMAGESET_NAME + ', bash script exited with fail')
                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'error-processing')
            except Exception as e:
                worsica_logger.info('[startProcessing processing]: error on trying to process ' + IMAGESET_NAME + ': ' + str(e))
                views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'error-processing')
        else:
            worsica_logger.info('[startProcessing processing]: MergedProcessImageSet ' + str(MPIS_ID) + ' state is not merged or error-processing. Skip.')
    except Exception as e:
        worsica_logger.info('[startProcessing processing]: MergedProcessImageSet ' + str(MPIS_ID) + ' not found: ' + str(e))

# ----------water leak------------


def _generate_climatology_avgmergedimageset(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, WATER_INDEX, LOGS_FOLDER, LOG_FILENAME, req_host):
    if SERVICE == 'waterleak':
        try:
            mpis = worsica_api_models.AverageMergedProcessImageSet.objects.get(id=MPIS_ID)
            if mpis.processState in ['submitted', 'error-generating-average']:
                # do the climatology with the stored-processing/success merged imagesets
                LIST_MERGED_IMAGESETS = [worsica_api_models.MergedProcessImageSet.objects.get(id=int(i)).name for i in mpis.mergedprocImageSet_ids.split(
                    ',') if worsica_api_models.MergedProcessImageSet.objects.get(id=int(i)).processState in ['success']]
                if (len(LIST_MERGED_IMAGESETS) > 0):
                    MERGED_IMAGESETS_NAME = ','.join(LIST_MERGED_IMAGESETS)
                    AVERAGE_IMAGE_NAME = mpis.name
                    try:
                        views._change_imageset_state(False, mpis, 'generating-average')
                        LOG_FILE = LOG_FILENAME + '-' + str(mpis.id) + '-generating-average.out'
                        COMMAND_SHELL = utils._build_command_shell_to_run(
                            './worsica_generating_climatology_service_v2.sh ' + SERVICE + ' ' + USER_ID + ' ' + ROI_ID + ' ' + SIMULATION_ID +
                            ' ' + PREV_JOB_SUBMISSION_STATE + ' ' + MERGED_IMAGESETS_NAME + ' ' + AVERAGE_IMAGE_NAME + ' ' + WATER_INDEX,
                            LOGS_FOLDER + '/' + LOG_FILE,
                            settings.ENABLE_GRID)
                        cmd_timeout = 60 * 60 * 24 * 4 if settings.ENABLE_GRID else 15 * 60  # minutes to avoid download script being stuck
                        worsica_logger.info('[startProcessing generating-average]: command_shell=' + str(COMMAND_SHELL))
                        cmd2 = subprocess.Popen(COMMAND_SHELL, cwd=settings.WORSICA_FOLDER_PATH + '/worsica_web_products/', shell=False)
                        cmd2_wait = cmd2.wait(timeout=cmd_timeout)
                        print('[debug cmd2_wait]: ' + str(cmd2_wait))
                        out2File = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/log/' + LOG_FILE
                        if cmd2_wait == 0 and views._find_word_in_output_file("state: generating-average", out2File):
                            print('[startProcessing generating-average]: imageset ' + AVERAGE_IMAGE_NAME + ' generated climatology')
                            views._change_imageset_state(False, mpis, 'generated-average')
                        else:
                            worsica_logger.info('[startProcessing generating-average]: error on trying to generate climatology ' + AVERAGE_IMAGE_NAME + ', bash script exited with fail')
                            views._change_imageset_state(False, mpis, 'error-generating-average')
                    except Exception as e:
                        worsica_logger.info('[startProcessing generating-average]: error on trying to generate climatology ' + AVERAGE_IMAGE_NAME + ': ' + str(e))
                        views._change_imageset_state(False, mpis, 'error-generating-average')
                else:
                    worsica_logger.info('[startProcessing generating-average]: error on trying to generate climatology: No merged imagesets were processed successfully')
                    views._change_imageset_state(False, mpis, 'error-generating-average')
            else:
                worsica_logger.info('[startProcessing generating-average]: AverageMergedProcessImageSet ' + str(MPIS_ID) + ' state is not submitted or error-generating-average. Skip.')
        except Exception as e:
            worsica_logger.info('[startProcessing generating-average]: AverageMergedProcessImageSet ' + str(MPIS_ID) + ' not found: ' + str(e))
    else:
        worsica_logger.info('[startProcessing generating-average]: Climatology generation only available on waterleak service. Skip.')
# ----------------------------


def _store_mergedimageset(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_USERCHOSEN_ID, PREV_JOB_SUBMISSION_STATE, WATER_INDEX, IS_CLIMATOLOGY, IS_USERCHOSEN_IMAGE, req_host):
    try:
        WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + ('/userchosen' if IS_USERCHOSEN_IMAGE else '/simulation') + str(SIMULATION_USERCHOSEN_ID)
        NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER

        if SERVICE == 'waterleak' and IS_CLIMATOLOGY:  # climatologies
            worsica_logger.info('[startProcessing storage]: AverageMergedProcessImageSet')
            mpis = worsica_api_models.AverageMergedProcessImageSet.objects.get(id=MPIS_ID)
        elif SERVICE == 'waterleak' and IS_USERCHOSEN_IMAGE:  # USER CHOSEN
            worsica_logger.info('[startProcessing storage]: UserChosenMergedProcessImageSet')
            mpis = worsica_api_models.UserChosenMergedProcessImageSet.objects.get(id=MPIS_ID)
        else:
            worsica_logger.info('[startProcessing storage]: MergedProcessImageSet')
            mpis = worsica_api_models.MergedProcessImageSet.objects.get(id=MPIS_ID)

        if mpis.processState in ['merged', 'error-storing-rgb', 'processed', 'error-storing-processing', 'generated-average', 'error-storing-generating-average']:  # 'error-storing'
            IMAGESET_NAME = mpis.name
            try:
                if mpis.processState in ['merged', 'error-storing-rgb']:
                    STORING_STEP = 'rgb'
                elif mpis.processState in ['processed', 'error-storing-processing']:
                    STORING_STEP = 'processing'
                elif mpis.processState in ['generated-average', 'error-storing-generating-average']:
                    STORING_STEP = 'generating-average'
                views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'storing-' + STORING_STEP)
                zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + ('-uc' if IS_USERCHOSEN_IMAGE else '-s') + \
                    str(SIMULATION_USERCHOSEN_ID) + '-' + IMAGESET_NAME + '-final-products.zip'
                r = requests.get(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + zipFileName, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
                worsica_logger.info('[startProcessing storage]: download ' + zipFileName)
                tmpPathZipFileName = '/tmp/' + zipFileName
                if r.status_code == 200:
                    with open(tmpPathZipFileName, 'wb') as f:
                        f.write(r.content)
                worsica_logger.info('[startProcessing storage]: unzip ' + zipFileName)
                zipF = zipfile.ZipFile(tmpPathZipFileName, 'r')
                zipF.extractall('/tmp/')
                output_path = '/tmp/' + WORKSPACE_SIMULATION_FOLDER
                # RGB (only coastal/inland)
                if (STORING_STEP == 'rgb'):
                    if SERVICE != 'waterleak':
                        itype = ['RGB', 'rgb']
                        _store_raster(mpis, SERVICE, USER_ID, ROI_ID, SIMULATION_USERCHOSEN_ID, IMAGESET_NAME, IS_CLIMATOLOGY, False, itype, output_path)
                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'stored-' + STORING_STEP)
                # WIs
                elif (STORING_STEP == 'processing' or STORING_STEP == 'generating-average'):
                    wis = WATER_INDEX.split(',')
                    for wi in wis:
                        # -------------------rasters---------------------------------
                        if SERVICE == 'waterleak':
                            indexTypes = [[wi, wi], ]
                        else:
                            indexTypes = [[wi, wi], [wi + '_binary', wi + '_bin'], [wi + '_binary_closing_edge_detection', wi + '_ced'], ]
                        for itype in indexTypes:
                            _store_raster(mpis, SERVICE, USER_ID, ROI_ID, SIMULATION_USERCHOSEN_ID, IMAGESET_NAME, IS_CLIMATOLOGY, False, itype, output_path, IS_USERCHOSEN_IMAGE)

                        # -------------------shapeline-------------------------------
                        # only coastal/inland
                        if SERVICE != 'waterleak' and not IS_USERCHOSEN_IMAGE:
                            _store_shapeline(mpis, IMAGESET_NAME, wi, output_path)
                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'stored-' + STORING_STEP)
                    views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'success')
            except Exception as e:
                worsica_logger.info('[startProcessing storing]: error on trying to process ' + IMAGESET_NAME + ': ' + str(e))
                views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'error-storing-' + STORING_STEP)
        else:
            worsica_logger.info('[startProcessing storing]: AverageMergedProcessImageSet/MergedProcessImageSet ' + str(MPIS_ID) + ' state is not processed or error-storing. Skip.')
    except Exception as e:
        worsica_logger.info('[startProcessing storing]: AverageMergedProcessImageSet/MergedProcessImageSet ' + str(MPIS_ID) + ' not found: ' + str(e))


def _store_raster(_obj_mpis_gt, SERVICE, USER_ID, ROI_ID, SIMULATION_USERCHOSEN_ID, IMAGESET_NAME, IS_CLIMATOLOGY, IS_LEAK, itype, output_path,
                  IS_USERCHOSEN_IMAGE=False, IS_TOPOGRAPHY=False, IS_FLOODFREQ=False, CLONE_AND_CLEAN_FROM=None, CLONE_AND_CLEAN_NAME=None):
    isFromManualThreshold = ('_threshold' in itype[0])  # when cloning, flag this if source is from manual threshold
    if (IS_LEAK):
        # /waterleak/roiAAA/simulationBBB/ldCCC/merged_resampled_XXXX
        nameRl = SERVICE + '-u' + str(USER_ID) + '-r' + str(ROI_ID) + ('-uc' if IS_USERCHOSEN_IMAGE else '-s') + \
            str(SIMULATION_USERCHOSEN_ID) + '-ld' + str(_obj_mpis_gt.id) + '-' + IMAGESET_NAME + '-' + itype[1]
        imagePath = output_path + '/ld' + str(_obj_mpis_gt.id) + '/' + IMAGESET_NAME + '_' + itype[0] + '.tif'
    elif (SERVICE == 'coastal' and IS_TOPOGRAPHY):
        nameRl = SERVICE + '-u' + str(USER_ID) + '-r' + str(ROI_ID) + '-s' + str(SIMULATION_USERCHOSEN_ID) + '-gt' + str(_obj_mpis_gt.id) + '-topo-' + itype[0]
        imagePath = output_path + '/gt' + str(_obj_mpis_gt.id) + '/' + IMAGESET_NAME + '_' + itype[0] + '.tif'
    elif (SERVICE == 'coastal' and IS_FLOODFREQ):
        nameRl = SERVICE + '-u' + str(USER_ID) + '-r' + str(ROI_ID) + '-s' + str(SIMULATION_USERCHOSEN_ID) + '-gt' + str(_obj_mpis_gt.id) + '-floodfreq-' + itype[0]
        imagePath = output_path + '/gt' + str(_obj_mpis_gt.id) + '/' + IMAGESET_NAME + '_' + itype[0] + '.tif'
    elif (SERVICE == 'coastal' and CLONE_AND_CLEAN_FROM == 'processing'):  # create a new shp
        nameRl = SERVICE + '-u' + str(USER_ID) + '-r' + str(ROI_ID) + ('-uc' if IS_USERCHOSEN_IMAGE else '-s') + str(SIMULATION_USERCHOSEN_ID) + '-' + IMAGESET_NAME + '-' + itype[1] + '-cleanup-tmp'
        imagePath = output_path + '/' + IMAGESET_NAME + '/' + IMAGESET_NAME + '_' + itype[0] + '.tif'
    elif (SERVICE == 'coastal' and CLONE_AND_CLEAN_FROM == 'cloning_and_cleaning'):  # create from an existing shp
        nameRl = SERVICE + '-u' + str(USER_ID) + '-r' + str(ROI_ID) + ('-uc' if IS_USERCHOSEN_IMAGE else '-s') + str(SIMULATION_USERCHOSEN_ID) + \
            '-' + IMAGESET_NAME + '-' + itype[1].replace('_cleanup', '') + '-cleanup-tmp'
        IMAGESET_NAME_FOLDER = IMAGESET_NAME + '_' + itype[0].split('_')[0] + '_cleanup'
        imagePath = output_path + '/' + IMAGESET_NAME_FOLDER + '/' + IMAGESET_NAME + '_' + itype[0] + '.tif'
    elif (SERVICE == 'coastal' and CLONE_AND_CLEAN_FROM == 'generate_threshold'):  # create raster after threshold
        nameRl = SERVICE + '-u' + str(USER_ID) + '-r' + str(ROI_ID) + ('-uc' if IS_USERCHOSEN_IMAGE else '-s') + str(SIMULATION_USERCHOSEN_ID) + \
            '-' + IMAGESET_NAME + '-' + itype[1].replace('_threshold', '') + '-threshold-tmp'
        imagePath = output_path + '/' + IMAGESET_NAME + '/' + IMAGESET_NAME + '_' + itype[0] + '.tif'
    else:
        nameRl = SERVICE + '-u' + str(USER_ID) + '-r' + str(ROI_ID) + ('-uc' if IS_USERCHOSEN_IMAGE else '-s') + str(SIMULATION_USERCHOSEN_ID) + '-' + IMAGESET_NAME + '-' + itype[1]
        imagePath = output_path + '/' + IMAGESET_NAME + '/' + IMAGESET_NAME + '_' + itype[0] + '.tif'

    if isFromManualThreshold:
        print('isFromManualThreshold')
        nameRl = SERVICE + '-u' + str(USER_ID) + '-r' + str(ROI_ID) + ('-uc' if IS_USERCHOSEN_IMAGE else '-s') + str(SIMULATION_USERCHOSEN_ID) + '-' + \
            IMAGESET_NAME + '-' + itype[1].replace('_cleanup', '').replace('_threshold', '') + '-threshold-cleanup-tmp'

    print('------------------------')
    try:
        wrapped_file = open(imagePath, 'rb')
        print('[startProcessing _store_raster] File ' + imagePath + ' found, store it')
        try:
            print('[startProcessing _store_raster] check Rasterlayer ' + nameRl)
            rl = raster_models.RasterLayer.objects.get(name=nameRl)
            # #on clone coastline, do not create new rasterlayer
            print('[startProcessing _store_raster] found it, delete')
            rl.delete()
            print('[startProcessing _store_raster] create again')
            rl = raster_models.RasterLayer.objects.create(name=nameRl)
        except Exception as e:
            print('[startProcessing _store_raster] not found, create')
            rl = raster_models.RasterLayer.objects.create(name=nameRl)
        rl.rasterfile.save('new', File(wrapped_file))
        rl.save()
        print(rl.id)
        wrapped_file.close()
        wrapped_file = None
        #Override existing histogram
        print('Fix existing Histogram...')
        wrapped_file = gdal.Open(imagePath)
        for i in range(1, wrapped_file.RasterCount+1):
            print(i)
            data = wrapped_file.GetRasterBand(i).ReadAsArray()
            datamax = np.nanmax(data) #find max, excluding nans
            datamin = np.nanmin(data) #find min, excluding nans
            hv,hb = np.histogram(data, range=(math.floor(datamin), math.ceil(datamax)), bins=100)
            print(hv, hb)
            print(sum(hv))
            md = raster_models.RasterLayerBandMetadata.objects.get(rasterlayer_id=rl.id, band=i-1)
            md.hist_values = hv.tolist()
            md.hist_bins = hb.tolist()
            md.save()
        wrapped_file = None
        print(SERVICE)
        print(CLONE_AND_CLEAN_FROM)
        print('[startProcessing _store_raster] storing done (id=' + str(rl.id) + ')')
        if SERVICE == 'waterleak' and IS_CLIMATOLOGY:  # climatologies
            raster, _ = worsica_api_models.AverageMergedProcessImageSetRaster.objects.get_or_create(
                name=nameRl,
                amprocessImageSet=_obj_mpis_gt,
                indexType=itype[0])
        elif SERVICE == 'waterleak' and IS_USERCHOSEN_IMAGE:  # uc
            raster, _ = worsica_api_models.UserChosenMergedProcessImageSetRaster.objects.get_or_create(
                name=nameRl,
                processImageSet=_obj_mpis_gt,
                indexType=itype[0])
        elif SERVICE == 'waterleak' and IS_LEAK:
            itype0split = itype[0].split('_', 1)  # wi + [anomaly/2nd_deriv/anomaly_2nd_deriv]
            raster, _ = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.get_or_create(
                name=nameRl,
                processImageSet=_obj_mpis_gt,
                ldType=itype0split[1])
        elif SERVICE == 'coastal' and (IS_TOPOGRAPHY or IS_FLOODFREQ):
            raster, _ = worsica_api_models.GenerateTopographyMapRaster.objects.get_or_create(
                name=nameRl,
                generateTopographyMap=_obj_mpis_gt,
                topoKind=('flood-freq' if IS_FLOODFREQ else 'topo'),
                indexType=itype[0])
        elif (SERVICE == 'coastal' and CLONE_AND_CLEAN_FROM == 'generate_threshold'):
            print('ImageSetThresholdRaster')
            raster, _ = worsica_api_models.ImageSetThresholdRaster.objects.get_or_create(
                name=nameRl,
                imageSetThreshold=_obj_mpis_gt,
                indexType=itype[0].replace('_threshold', '')  # remove _threshold
            )
        else:
            raster, _ = worsica_api_models.MergedProcessImageSetRaster.objects.get_or_create(
                name=nameRl,
                processImageSet=_obj_mpis_gt,
                indexType=itype[0].replace('_threshold', ''),  # remove _threshold
                isFromManualThreshold=isFromManualThreshold,
                clone_cleanup_small_name=CLONE_AND_CLEAN_NAME if CLONE_AND_CLEAN_NAME else '')
        # auto threshold (coastline/inland), except topographies
        if SERVICE != 'waterleak' and not IS_TOPOGRAPHY and not IS_FLOODFREQ:
            if (SERVICE == 'coastal' and CLONE_AND_CLEAN_FROM == 'generate_threshold'):
                raster.thresholdValue = float(raster.imageSetThreshold.threshold)
            else:
                try:
                    print('[startProcessing register autothreshold value] read ' + IMAGESET_NAME + '_' + itype[0] + '_auto_threshold_val.txt')
                    with open(output_path + '/' + IMAGESET_NAME + '/' + IMAGESET_NAME + '_' + itype[0] + '_auto_threshold_val.txt', 'r') as f:
                        frl = f.readlines()
                        if (len(frl) > 0):
                            raster.autoThresholdValue = float(frl[0])
                except Exception as e:
                    print('[startProcessing register autothreshold value] ' + IMAGESET_NAME + '_' + itype[0] + '_auto_threshold_val.txt fail')
                    print(e)

        raster.rasterLayer = rl
        raster.save()
        print('[startProcessing _store_raster] delete ' + raster.rasterLayer.rasterfile.name + '...')
        if os.path.exists(settings.WORSICA_FOLDER_PATH + '/worsica_web_intermediate/' + raster.rasterLayer.rasterfile.name):
            os.remove(settings.WORSICA_FOLDER_PATH + '/worsica_web_intermediate/' + raster.rasterLayer.rasterfile.name)
            print('[startProcessing _store_raster] deleted!')
        else:
            print('[startProcessing _store_raster] cannot delete, not found, ignore')
        del raster, rl, wrapped_file
    except Exception as e:
        print(traceback.format_exc())
        print('[startProcessing _store_raster] An error occured! ' + str(e))
    print('------------------------')


def _store_shapeline(mpis, IMAGESET_NAME, wi, output_path, CLONE_AND_CLEAN_FROM=None, CUSTOM_NAME='custom_name', imageSetThreshold_id=None):
    print('------------------------')
    try:
        # trigger a pre-save signal to set a foreign key of the shapeline
        def pre_save_shapeline_callback(sender, instance, *args, **kwargs):
            instance.shapelineMPIS = shapeline_group
            instance.name = IMAGESET_NAME + '_' + wi

        shp_mapping = {
            'dn': 'DN',
            'dn_1': 'DN_1',
            'mls': 'LINESTRING'
        }
        IMAGESET_NAME_FOLDER = IMAGESET_NAME
        if (CLONE_AND_CLEAN_FROM in ['cloning_and_cleaning', 'processing']):  # not none
            print(CLONE_AND_CLEAN_FROM)
            if CLONE_AND_CLEAN_FROM == 'cloning_and_cleaning':
                IMAGESET_NAME_FOLDER = IMAGESET_NAME + '_' + wi.replace('_threshold', '')
            worsica_logger.info('[startProcessing _store_shapeline]: get shpline ' + IMAGESET_NAME + '_' + wi + ' and clone it as ' + CUSTOM_NAME + '...')
            wi2 = wi.replace('_cleanup', '')  # .replace('_threshold','') #remove cleanup
            print(IMAGESET_NAME + '_' + wi2)
            actual_shapeline_group = worsica_api_models.ShapelineMPIS.objects.get(mprocessImageSet=mpis, name=IMAGESET_NAME + '_' + wi2)
            print(actual_shapeline_group.id)
            name_ref = actual_shapeline_group.name + '_cleanup_tmp'
            shapeline_group, _ = worsica_api_models.ShapelineMPIS.objects.get_or_create(
                mprocessImageSet=actual_shapeline_group.mprocessImageSet, name=name_ref, reference=name_ref, shapelineSourceType='cloning_and_cleaning')
            shapeline_group.small_name = slugify(CUSTOM_NAME)
            shapeline_group.save()
        else:
            worsica_logger.info('[startProcessing _store_shapeline]: deleting any existing shplines before store ' + IMAGESET_NAME + '_' + wi + '...')
            name_ref = IMAGESET_NAME + '_' + wi
            shapeline_group, _ = worsica_api_models.ShapelineMPIS.objects.get_or_create(mprocessImageSet=mpis, name=name_ref, reference=name_ref)
            shapeline_group.isFromManualThreshold = (CLONE_AND_CLEAN_FROM == 'generate_threshold')
            if (CLONE_AND_CLEAN_FROM == 'generate_threshold' and imageSetThreshold_id is not None):
                iST = worsica_api_models.ImageSetThreshold.objects.get(id=imageSetThreshold_id)
                shapeline_group.imageSetThreshold = iST
                shapeline_group.name = IMAGESET_NAME + '_' + wi + '_tmp'  # set _tmp prefix
                shapeline_group.reference = IMAGESET_NAME + '_' + wi + '_tmp'
            shapeline_group.small_name = slugify(CUSTOM_NAME)
            shapeline_group.save()
            old_shapeline_group = worsica_api_models.Shapeline.objects.filter(shapelineMPIS=shapeline_group, name=IMAGESET_NAME + '_' + wi)
            old_shapeline_group.delete()
        worsica_logger.info('[startProcessing _store_shapeline]: trying to store ' + IMAGESET_NAME + '_' + wi + '...')
        shapelinePath = output_path + '/' + IMAGESET_NAME_FOLDER + '/' + IMAGESET_NAME + '_' + wi + '.shp'
        worsica_logger.info('[startProcessing _store_shapeline]: shp_path=' + shapelinePath)

        lm = LayerMapping(worsica_api_models.Shapeline, shapelinePath, shp_mapping, transform=True, encoding='iso-8859-1', )
        pre_save.connect(pre_save_shapeline_callback, sender=worsica_api_models.Shapeline)
        lm.save(verbose=True)
        pre_save.disconnect(pre_save_shapeline_callback, sender=worsica_api_models.Shapeline)
        worsica_logger.info('[startProcessing _store_shapeline]: ' + IMAGESET_NAME + '_' + wi + ' shp successfully stored')
    except Exception as e:
        print(traceback.format_exc())
        worsica_logger.info('[startProcessing _store_shapeline]: error on trying to store shapefiles (' + str(e) + ')')
    print('------------------------')

# --------------- water leak detection --------------
# calculate the anomaly (anomaly = index - climatology) before applying 2nd derivative


def _process_mergedimageset_leakdetection_anomaly(LEAK_DETECTION_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, req_host):
    if SERVICE == 'waterleak' and PREV_JOB_SUBMISSION_STATE == 'success':
        try:
            mpis = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(id=LEAK_DETECTION_ID)  # , start_processing_second_deriv_from = 'by_anomaly')
            WATER_INDEX = mpis.indexType
            DETERMINE_LEAK_BY = mpis.start_processing_second_deriv_from
            IS_USERCHOSEN_IMAGE = (mpis.ucprocessImageSet is not None and mpis.processImageSet is None)
            print('[debug IS_USERCHOSEN_IMAGE]: ' + str(IS_USERCHOSEN_IMAGE))
            mpis_pis = (mpis.ucprocessImageSet if IS_USERCHOSEN_IMAGE else mpis.processImageSet)
            if mpis.processState in ['submitted', 'stored', 'error-calculating-diff', 'error-storing-calculated-diff']:
                # calculating-diff
                MERGED_IMAGE_NAME = mpis_pis.name
                LD_IMAGE_NAME = 'ld' + str(mpis.id)
                if mpis.processState in ['submitted', 'stored', 'error-calculating-diff']:
                    print(mpis_pis.sensingDate.strftime("%Y-%m-%d"))
                    CLIMATOLOGY_SEARCH_PERIOD = 7
                    print('Searching for a nearest climatology by ' + str(CLIMATOLOGY_SEARCH_PERIOD) + ' days back...')
                    AVERAGE_IMAGE = None
                    for day in range(0, CLIMATOLOGY_SEARCH_PERIOD + 1):
                        mpis_pis_sd = mpis_pis.sensingDate - datetime.timedelta(days=day)
                        MERGED_IMAGE_DATE = mpis_pis_sd.strftime("%Y-%m-%d").split('-')
                        print(MERGED_IMAGE_DATE[1] + '-' + MERGED_IMAGE_DATE[2])
                        try:
                            GET_AVERAGE_IMAGE = worsica_api_models.AverageMergedProcessImageSet.objects.get(jobSubmission=mpis.jobSubmission, date=MERGED_IMAGE_DATE[1] + '-' + MERGED_IMAGE_DATE[2])
                            print('Stop search! Found a compatible climatology: ' + GET_AVERAGE_IMAGE.name)
                            AVERAGE_IMAGE = GET_AVERAGE_IMAGE
                            break
                        except Exception as e:
                            print(str(e))
                            pass
                    # not sure why mpis.procimageset.jobsubmission???
                    if AVERAGE_IMAGE is not None:
                        AVERAGE_IMAGE_NAME = AVERAGE_IMAGE.name
                        try:
                            PREV_MERGED_IMAGE_STATE = mpis.processState
                            views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'calculating-diff')
                            LOG_FILE = LOG_FILENAME + '-' + str(mpis.id) + '-calculating-diff.out'
                            COMMAND_SHELL = utils._build_command_shell_to_run(
                                './worsica_calculate_anomaly' + ('_uc' if IS_USERCHOSEN_IMAGE else '') + '_v2.sh ' + SERVICE + ' ' + USER_ID + ' ' + ROI_ID + ' ' + SIMULATION_ID + ' ' + PREV_MERGED_IMAGE_STATE + ' ' +
                                MERGED_IMAGE_NAME + ' ' + AVERAGE_IMAGE_NAME + ' ' + WATER_INDEX + ' ' + LD_IMAGE_NAME +
                                (' ' + str(mpis.ucprocessImageSet.user_chosen_product.id) if IS_USERCHOSEN_IMAGE else ''),
                                LOGS_FOLDER + '/' + LOG_FILE,
                                False)
                            cmd_timeout = 15 * 60  # minutes to avoid download script being stuck
                            worsica_logger.info('[startProcessing calculating-diff]: command_shell=' + str(COMMAND_SHELL))
                            cmd2 = subprocess.Popen(COMMAND_SHELL, cwd=settings.WORSICA_FOLDER_PATH + '/worsica_web_products/', shell=False)
                            cmd2_wait = cmd2.wait(timeout=cmd_timeout)
                            print('[debug cmd2_wait]: ' + str(cmd2_wait))
                            out2File = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/log/' + LOG_FILE
                            if cmd2_wait == 0 and views._find_word_in_output_file("state: calculated-diff", out2File):
                                print('[startProcessing calculating-diff]: imageset ' + MERGED_IMAGE_NAME + ' calculated-diff')
                                views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'calculated-diff')
                            else:
                                worsica_logger.info('[startProcessing calculating-diff]: error on trying to calculate anomaly products ' + MERGED_IMAGE_NAME + ', bash script exited with fail')
                                views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'error-calculating-diff')
                        except Exception as e:
                            worsica_logger.info('[startProcessing calculating-diff]: error on trying to calculate anomaly products ' + MERGED_IMAGE_NAME + ': ' + str(e))
                            views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'error-calculating-diff')
                    else:
                        worsica_logger.info('[startProcessing calculating-diff]: climatology not found that matches with ' + MERGED_IMAGE_NAME + ' date: ' + str(e))
                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'error-calculating-diff')
                # storing
                if mpis.processState in ['calculated-diff', 'error-storing-calculated-diff']:
                    try:
                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'storing-calculated-diff')
                        IMAGESET_NAME = mpis_pis.name  # name of merged imageset
                        LD_IMAGE_NAME = 'ld' + str(mpis.id)
                        WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
                        NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER
                        zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + LD_IMAGE_NAME + '-final-products.zip'
                        r = requests.get(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + zipFileName, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
                        worsica_logger.info('[startProcessing storage]: download ' + zipFileName)
                        tmpPathZipFileName = '/tmp/' + zipFileName
                        if r.status_code == 200:
                            with open(tmpPathZipFileName, 'wb') as f:
                                f.write(r.content)
                        worsica_logger.info('[startProcessing storage]: unzip ' + zipFileName)
                        zipF = zipfile.ZipFile(tmpPathZipFileName, 'r')
                        zipF.extractall('/tmp/')
                        output_path = '/tmp/' + WORKSPACE_SIMULATION_FOLDER
                        wis = WATER_INDEX.split(',')
                        for wi in wis:
                            indexTypes = [[wi + '_anomaly', wi + '_anomaly'], ]
                            for itype in indexTypes:
                                _store_raster(mpis, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, IMAGESET_NAME, False, True, itype, output_path)
                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'stored-calculated-diff')
                    except Exception as e:
                        print(traceback.format_exc())
                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'error-storing-calculated-diff')
            else:
                worsica_logger.info('[startProcessing calculating-diff]: MergedProcessImageSetForLeakDetection ' + str(LEAK_DETECTION_ID) +
                                    ' state is not submitted, stored, error-storing-calculated-diff or error-calculating-diff. Skip.')
        except Exception as e:
            print(traceback.format_exc())
            worsica_logger.info('[startProcessing calculating-diff]: MergedProcessImageSetForLeakDetection ' + str(LEAK_DETECTION_ID) + ' error: ' + str(e))
            views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'error-calculating-diff')
    else:
        worsica_logger.info('[startProcessing calculating-diff]: Anomaly calculation only available on waterleak service and only if job submission was successful. Skip.')

# calculate 2nd deriv


def _process_mergedimageset_leakdetection_second_deriv(LEAK_DETECTION_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, req_host):
    if SERVICE == 'waterleak' and PREV_JOB_SUBMISSION_STATE == 'success':
        try:
            mpis = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(id=LEAK_DETECTION_ID)  # , start_processing_second_deriv_from = DETERMINE_LEAK_BY)
            IS_USERCHOSEN_IMAGE = (mpis.ucprocessImageSet is not None and mpis.processImageSet is None)
            print('[debug IS_USERCHOSEN_IMAGE]: ' + str(IS_USERCHOSEN_IMAGE))
            WATER_INDEX = mpis.indexType
            DETERMINE_LEAK_BY = mpis.start_processing_second_deriv_from
            mpis_pis = (mpis.ucprocessImageSet if IS_USERCHOSEN_IMAGE else mpis.processImageSet)
            if ((DETERMINE_LEAK_BY == 'by_anomaly' and mpis.processState in ['stored-calculated-diff'])
                    or (DETERMINE_LEAK_BY == 'by_index' and mpis.processState in ['submitted', 'stored'])
                    or mpis.processState in ['error-calculating-leak', 'error-storing-calculated-leak']):
                MERGED_IMAGE_NAME = mpis_pis.name
                LD_IMAGE_NAME = 'ld' + str(mpis.id)
                if mpis.processState in ['submitted', 'stored', 'stored-calculated-diff', 'error-calculating-leak']:
                    try:
                        PREV_MERGED_IMAGE_STATE = mpis.processState
                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'calculating-leak')
                        LOG_FILE = LOG_FILENAME + '-' + str(mpis.id) + '-calculating-leak.out'
                        COMMAND_SHELL = utils._build_command_shell_to_run(
                            './worsica_calculate_second_deriv' + ('_uc' if IS_USERCHOSEN_IMAGE else '') + '_v2.sh ' + SERVICE + ' ' + USER_ID + ' ' + ROI_ID + ' ' + SIMULATION_ID + ' ' + PREV_MERGED_IMAGE_STATE + ' ' +
                            MERGED_IMAGE_NAME + ' ' + WATER_INDEX + ' ' + DETERMINE_LEAK_BY + ' ' + LD_IMAGE_NAME +
                            (' ' + str(mpis.ucprocessImageSet.user_chosen_product.id) if IS_USERCHOSEN_IMAGE else ''),
                            LOGS_FOLDER + '/' + LOG_FILE,
                            False)
                        cmd_timeout = 15 * 60  # minutes to avoid download script being stuck
                        worsica_logger.info('[startProcessing calculating-leak]: command_shell=' + str(COMMAND_SHELL))
                        cmd2 = subprocess.Popen(COMMAND_SHELL, cwd=settings.WORSICA_FOLDER_PATH + '/worsica_web_products/', shell=False)
                        cmd2_wait = cmd2.wait(timeout=cmd_timeout)
                        print('[debug cmd2_wait]: ' + str(cmd2_wait))
                        out2File = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/log/' + LOG_FILE
                        if cmd2_wait == 0 and views._find_word_in_output_file("state: calculated-leak", out2File):
                            print('[startProcessing calculating-leak]: imageset ' + MERGED_IMAGE_NAME + ' calculated-leak')
                            views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'calculated-leak')
                        else:
                            worsica_logger.info('[startProcessing calculating-leak]: error on trying to calculate 2nd deriv products ' + MERGED_IMAGE_NAME + ', bash script exited with fail')
                            views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'error-calculating-leak')
                    except Exception as e:
                        worsica_logger.info('[startProcessing calculating-leak]: error on trying to calculate 2nd deriv products ' + MERGED_IMAGE_NAME + ': ' + str(e))
                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'error-calculating-leak')
                # storing
                if mpis.processState in ['calculated-leak', 'error-storing-calculated-leak']:
                    try:
                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'storing-calculated-leak')
                        IMAGESET_NAME = mpis_pis.name  # name of merged imageset
                        LD_IMAGE_NAME = 'ld' + str(mpis.id)
                        WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
                        NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER
                        zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + LD_IMAGE_NAME + '-final-products.zip'
                        r = requests.get(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + zipFileName, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
                        worsica_logger.info('[startProcessing storage]: download ' + zipFileName)
                        tmpPathZipFileName = '/tmp/' + zipFileName
                        if r.status_code == 200:
                            with open(tmpPathZipFileName, 'wb') as f:
                                f.write(r.content)
                        worsica_logger.info('[startProcessing storage]: unzip ' + zipFileName)
                        zipF = zipfile.ZipFile(tmpPathZipFileName, 'r')
                        zipF.extractall('/tmp/')
                        output_path = '/tmp/' + WORKSPACE_SIMULATION_FOLDER
                        wis = WATER_INDEX.split(',')
                        for wi in wis:
                            if (DETERMINE_LEAK_BY == 'by_anomaly'):
                                indexTypes = [[wi + '_anomaly_2nd_deriv', wi + '_anomaly_2nd_deriv'], ]
                            else:
                                indexTypes = [[wi + '_2nd_deriv', wi + '_2nd_deriv'], ]
                            for itype in indexTypes:
                                _store_raster(mpis, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, IMAGESET_NAME, False, True, itype, output_path)
                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'stored-calculated-leak')
                    except Exception as e:
                        print(traceback.format_exc())
                        views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'error-storing-calculated-leak')
            else:
                worsica_logger.info('[startProcessing calculating-leak]: MergedProcessImageSetForLeakDetection ' + str(LEAK_DETECTION_ID) +
                                    ' state is not submitted, stored, stored-calculated-diff or error-calculating-leak. Skip.')
        except Exception as e:
            print(traceback.format_exc())
            worsica_logger.info('[startProcessing calculating-leak]: MergedProcessImageSetForLeakDetection ' + str(LEAK_DETECTION_ID) + ' not found: ' + str(e))
            views._change_imageset_state(IS_USERCHOSEN_IMAGE, mpis, 'error-calculating-leak')
    else:
        worsica_logger.info('[startProcessing calculating-leak]: Second deriv calculation only available on waterleak service and only if job submission was successful. Skip.')

# identify leaks


def _process_mergedimageset_leakdetection_identifyleaks(LEAK_DETECTION_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, req_host):
    if SERVICE == 'waterleak' and PREV_JOB_SUBMISSION_STATE == 'success':
        try:
            mpis = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(id=LEAK_DETECTION_ID)  # , start_processing_second_deriv_from = DETERMINE_LEAK_BY)
            WATER_INDEX = mpis.indexType
            DETERMINE_LEAK_BY = mpis.start_processing_second_deriv_from
            IS_USERCHOSEN_IMAGE = (mpis.ucprocessImageSet is not None and mpis.processImageSet is None)
            print('[debug IS_USERCHOSEN_IMAGE]: ' + str(IS_USERCHOSEN_IMAGE))
            mpis_pis = (mpis.ucprocessImageSet if IS_USERCHOSEN_IMAGE else mpis.processImageSet)
            FLAG_FILTER_BY_MASK = ('filter_by_mask' if mpis.filter_by_mask else 'normal')
            if mpis.processState in ['stored-calculated-leak', 'stored-determinated-leak', 'generated-mask-leak', 'error-determinating-leak', 'error-storing-determinated-leak']:
                MERGED_IMAGE_NAME = mpis_pis.name
                LD_IMAGE_NAME = 'ld' + str(mpis.id)
                if mpis.processState in ['stored-determinated-leak', 'stored-calculated-leak', 'generated-mask-leak', 'error-determinating-leak']:
                    try:
                        PREV_MERGED_IMAGE_STATE = mpis.processState
                        views._change_imageset_state(False, mpis, 'determinating-leak')
                        LOG_FILE = LOG_FILENAME + '-' + str(mpis.id) + '-determinating-leak.out'
                        COMMAND_SHELL = utils._build_command_shell_to_run(
                            './worsica_identify_leaks_v2.sh ' + SERVICE + ' ' + USER_ID + ' ' + ROI_ID + ' ' + SIMULATION_ID + ' ' + PREV_MERGED_IMAGE_STATE +
                            ' ' + MERGED_IMAGE_NAME + ' ' + WATER_INDEX + ' ' + DETERMINE_LEAK_BY + ' ' + LD_IMAGE_NAME + ' ' + FLAG_FILTER_BY_MASK,
                            LOGS_FOLDER + '/' + LOG_FILE,
                            False)
                        cmd_timeout = 15 * 60  # minutes to avoid download script being stuck
                        worsica_logger.info('[startProcessing determinating-leak]: command_shell=' + str(COMMAND_SHELL))
                        cmd2 = subprocess.Popen(COMMAND_SHELL, cwd=settings.WORSICA_FOLDER_PATH + '/worsica_web_products/', shell=False)
                        cmd2_wait = cmd2.wait(timeout=cmd_timeout)
                        print('[debug cmd2_wait]: ' + str(cmd2_wait))
                        out2File = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/log/' + LOG_FILE
                        if cmd2_wait == 0 and views._find_word_in_output_file("state: determinating-leak", out2File):
                            print('[startProcessing determinating-leak]: imageset ' + MERGED_IMAGE_NAME + ' determinating-leak')
                            views._change_imageset_state(False, mpis, 'determinated-leak')
                        else:
                            worsica_logger.info('[startProcessing determinating-leak]: error on trying to identify potential leaks ' + MERGED_IMAGE_NAME + ', bash script exited with fail')
                            views._change_imageset_state(False, mpis, 'error-determinating-leak')
                    except Exception as e:
                        worsica_logger.info('[startProcessing determinating-leak]: error on trying to identify potential leaks ' + MERGED_IMAGE_NAME + ': ' + str(e))
                        views._change_imageset_state(False, mpis, 'error-determinating-leak')
                # storing
                if mpis.processState in ['determinated-leak', 'error-storing-determinated-leak']:
                    try:
                        views._change_imageset_state(False, mpis, 'storing-determinated-leak')
                        IMAGESET_NAME = mpis_pis.name  # name of merged imageset
                        LD_IMAGE_NAME = 'ld' + str(mpis.id)
                        WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
                        NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER
                        zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-' + LD_IMAGE_NAME + '-final-products.zip'
                        r = requests.get(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + zipFileName, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
                        worsica_logger.info('[startProcessing storage]: download ' + zipFileName)
                        tmpPathZipFileName = '/tmp/' + zipFileName
                        if r.status_code == 200:
                            with open(tmpPathZipFileName, 'wb') as f:
                                f.write(r.content)
                        worsica_logger.info('[startProcessing storage]: unzip ' + zipFileName)
                        zipF = zipfile.ZipFile(tmpPathZipFileName, 'r')
                        zipF.extractall('/tmp/')
                        output_path = '/tmp/' + WORKSPACE_SIMULATION_FOLDER
                        wis = WATER_INDEX.split(',')
                        try:
                            for wi in wis:
                                mpisr = worsica_api_models.MergedProcessImageSetForLeakDetectionRaster.objects.get(
                                    processImageSet=mpis,
                                    ldType=('anomaly_' if (DETERMINE_LEAK_BY == 'by_anomaly') else '') + '2nd_deriv'
                                )
                                worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPoints.objects.filter(second_deriv_raster=mpisr).delete()
                                with open(output_path + '/' + LD_IMAGE_NAME + '/' + IMAGESET_NAME + '_' + wi + '_' + ('anomaly_' if (DETERMINE_LEAK_BY == 'by_anomaly') else '') + '2nd_deriv_leaks.json', 'r') as f:
                                    jsonleaks = json.load(f)
                                    with transaction.atomic():  # speedup creation by doing atomic transaction, thus avoiding the autocommit
                                        for leak in jsonleaks:
                                            lp = worsica_api_models.MergedProcessImageSetForLeakDetectionRasterLeakPoints.objects.create(
                                                second_deriv_raster=mpisr,
                                                xCoordinate=leak['x'],
                                                yCoordinate=leak['y'],
                                                pixelValue=leak['value'],
                                            )
                                            lp.small_name = mpis.small_name + '-leakid' + str(lp.id)
                                            lp.save()
                                views._change_imageset_state(False, mpis, 'stored-determinated-leak')
                        except Exception as e:
                            print(traceback.format_exc())
                            views._change_imageset_state(False, mpis, 'error-storing-determinated-leak')

                    except Exception as e:
                        print(traceback.format_exc())
                        views._change_imageset_state(False, mpis, 'error-storing-determinated-leak')
            else:
                worsica_logger.info('[startProcessing determinating-leak]: MergedProcessImageSetForLeakDetection ' +
                                    str(LEAK_DETECTION_ID) + ' state is not stored-calculated-leak or error-determinating-leak. Skip.')
        except Exception as e:
            print(traceback.format_exc())
            worsica_logger.info('[startProcessing determinating-leak]: MergedProcessImageSetForLeakDetection ' + str(LEAK_DETECTION_ID) + ' not found: ' + str(e))
    else:
        worsica_logger.info('[startProcessing determinating-leak]: Identification of potential leaks only available on waterleak service and only if job submission was successful. Skip.')


# USER REPOSITORY
# MASKS
# Main function to run the image leak filtering (determine_image_leaks_with_mask)

# then subsubtasks._start_filter_leak_by_raster_mask(aos, PATH_TO_PRODUCTS, mask_source_ids)
def _process_mergedimageset_leakdetection_filterleaks(LEAK_DETECTION_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, req_host):
    # determine_image_leaks_with_mask(imageset, aos, PATH_TO_PRODUCTS, mask_source_ids):
    # https://www.programcreek.com/python/example/101827/gdal.RasterizeLayer (example 7)
    if SERVICE == 'waterleak' and PREV_JOB_SUBMISSION_STATE == 'success':
        try:
            mpis = worsica_api_models.MergedProcessImageSetForLeakDetection.objects.get(id=LEAK_DETECTION_ID)  # , start_processing_second_deriv_from = DETERMINE_LEAK_BY)
            IS_USERCHOSEN_IMAGE = (mpis.ucprocessImageSet is not None and mpis.processImageSet is None)
            print('[debug IS_USERCHOSEN_IMAGE]: ' + str(IS_USERCHOSEN_IMAGE))
            mpis_pis = (mpis.ucprocessImageSet if mpis.ucprocessImageSet is not None else mpis.processImageSet)
            IMAGESET_NAME = mpis_pis.name
            WATER_INDEX = mpis.indexType
            DETERMINE_LEAK_BY = mpis.start_processing_second_deriv_from
            print('==================')
            print(mpis.processState)
            print('==================')
            # if mpis.processState in ['cleaned-leak', 'stored-determinated-leak','error-generating-mask-leak','generated-mask-leak','error-cleaning-leak','error-cleaning-leak-nomask']:
            if mpis.processState in ['stored-determinated-leak', 'stored-calculated-leak', 'error-generating-mask-leak']:
                # check if there are compatible geometries to generate a mask, otherwise, skip this step
                try:
                    views._change_imageset_state(False, mpis, 'generating-mask-leak')
                    # calls subsubtasks._shp_to_raster_mask(user_id, roi_polygon)
                    job_submission = mpis.jobSubmission  # mpis.processImageSet.jobSubmission
                    aos_id = job_submission.aos_id
                    simulation_id = job_submission.id
                    ldmask_state = subsubtasks._shp_to_raster_mask(USER_ID, mpis)
                    if ldmask_state in ['generated-mask']:
                        views._change_imageset_state(False, mpis, 'generated-mask-leak')
                    else:
                        views._change_imageset_state(False, mpis, 'error-generating-mask-leak')
                except Exception as e:
                    print(traceback.format_exc())
                    views._change_imageset_state(False, mpis, 'error-generating-mask-leak')

            else:
                worsica_logger.info('[startProcessing determinating-leak]: MergedProcessImageSetForLeakDetection ' + str(LEAK_DETECTION_ID) +
                                    ' state is not cleaned-leak, stored-determinated-leak or error-cleaning-leak. Skip.')

        except Exception as e:
            print(traceback.format_exc())
            worsica_logger.info('[startProcessing cleaning-leak]: MergedProcessImageSetForLeakDetection ' + str(LEAK_DETECTION_ID) + ' not found: ' + str(e))


# GENERATE TOPOGRAPHY
def _generate_topography(GENERATE_TOPO_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, ZREF, COORDS, TIDE_FILENAME, WATER_INDEX, LOGS_FOLDER, LOG_FILENAME, req_host):
    if SERVICE == 'coastal' and PREV_JOB_SUBMISSION_STATE == 'success':
        try:
            gT = worsica_api_models.GenerateTopographyMap.objects.get(id=GENERATE_TOPO_ID)
            job_submission = gT.jobSubmission
            PREV_GENERATED_TOPO_STATE = gT.processState
            if gT.processState in ['submitted', 'error-generating-topo']:
                views._change_generate_topo_state(gT, 'generating-topo')
                mpiss = worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission, processState__in=['success']).order_by('sensingDate')
                LIST_MERGED_IMAGESETS = []
                for mpis in mpiss:
                    LIST_MERGED_IMAGESETS.append(mpis.name)  # get full zip with ndwi,mndwi,awei,aweish
                    print(mpis.name)
                    # check if there are cleanups, not the tmp ones
                    actual_shapeline_groups = worsica_api_models.ShapelineMPIS.objects.filter(mprocessImageSet=mpis).exclude(name__icontains='_tmp').order_by('-name')
                    # order descendent by name lists first the recent ones
                    # order must be _threshold_cleanup, _threshold, _cleanup
                    d = {}
                    for asg in actual_shapeline_groups:
                        wi = asg.name.replace('_threshold', '').replace('_cleanup', '').split('_')[-1]
                        if ('_threshold_cleanup' in asg.name) or ('_threshold' in asg.name) or ('_cleanup' in asg.name):
                            if wi not in d:
                                d[wi] = asg.name
                    for wi in d:
                        LIST_MERGED_IMAGESETS.append(d[wi])

                print(LIST_MERGED_IMAGESETS)

                if (len(LIST_MERGED_IMAGESETS) > 0):
                    MERGED_IMAGESETS_NAME = ','.join(LIST_MERGED_IMAGESETS)
                    GT_NAME = gT.name
                    GT_GENERATE_FROM = gT.generateTopographyFrom
                    try:
                        LOG_FILE = LOG_FILENAME + '-' + str(gT.id) + '-generating-topography.out'
                        COMMAND_SHELL = utils._build_command_shell_to_run(
                            './worsica_generating_topography_v2.sh ' + SERVICE + ' ' + USER_ID + ' ' + ROI_ID + ' ' + SIMULATION_ID + ' ' + GENERATE_TOPO_ID + ' ' + PREV_GENERATED_TOPO_STATE + ' ' + MERGED_IMAGESETS_NAME +
                            ' ' + GT_NAME + ' ' + ZREF + ' ' + ('point' if GT_GENERATE_FROM == 'probing' else 'tide') + ' ' + ('\"' + COORDS + '\"' if GT_GENERATE_FROM == 'probing' else TIDE_FILENAME),
                            LOGS_FOLDER + '/' + LOG_FILE,
                            False)
                        cmd_timeout = 15 * 60  # minutes to avoid download script being stuck
                        worsica_logger.info('[startTopographyGenerating _generate_topography]: command_shell=' + str(COMMAND_SHELL))
                        cmd2 = subprocess.Popen(COMMAND_SHELL, bufsize=0, cwd=settings.WORSICA_FOLDER_PATH + '/worsica_web_products/', shell=False)
                        cmd2_wait = cmd2.wait(timeout=cmd_timeout)
                        print('[debug cmd2_wait]: ' + str(cmd2_wait))
                        out2File = settings.WORSICA_FOLDER_PATH + '/worsica_web_products/log/' + LOG_FILE
                        if cmd2_wait == 0 and views._find_word_in_output_file("state: generated-topo", out2File):
                            print('[startTopographyGenerating _generate_topography]: topography ' + GT_NAME + ' generated')
                            views._change_generate_topo_state(gT, 'generated-topo')
                        else:
                            worsica_logger.info('[startTopographyGenerating _generate_topography]: error on trying to generate topography ' + GT_NAME + ', bash script exited with fail')
                            views._change_generate_topo_state(gT, 'error-generating-topo')
                    except Exception as e:
                        print(traceback.format_exc())
                        worsica_logger.info('[startTopographyGenerating _generate_topography]: error on trying to generate topography ' + GT_NAME + ': ' + str(e))
                        views._change_generate_topo_state(gT, 'error-generating-topo')
                else:
                    worsica_logger.info('[startTopographyGenerating _generate_topography]: error on trying to generate topography: No merged imagesets were processed successfully')
                    views._change_generate_topo_state(gT, 'error-generating-topo')
            #
            gT = worsica_api_models.GenerateTopographyMap.objects.get(id=GENERATE_TOPO_ID)
            if gT.processState in ['generated-topo', 'error-storing-generated-topo']:
                try:
                    views._change_generate_topo_state(gT, 'storing-generated-topo')
                    GT_NAME = gT.name

                    WORKSPACE_SIMULATION_FOLDER = SERVICE + '/user' + str(USER_ID) + '/roi' + str(ROI_ID) + '/simulation' + str(SIMULATION_ID)
                    NEXTCLOUD_PATH_TO_PRODUCTS = nextcloud_access.NEXTCLOUD_URL_PATH + '/' + WORKSPACE_SIMULATION_FOLDER + '/gt' + str(GENERATE_TOPO_ID)
                    zipFileName = SERVICE + '-user' + str(USER_ID) + '-roi' + str(ROI_ID) + '-s' + str(SIMULATION_ID) + '-gt' + str(gT.id) + '-' + GT_NAME + '-final-products.zip'
                    r = requests.get(NEXTCLOUD_PATH_TO_PRODUCTS + '/' + zipFileName, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
                    worsica_logger.info('[startTopographyGenerating _store_topography]: download ' + zipFileName)
                    tmpPathZipFileName = '/tmp/' + zipFileName
                    if r.status_code == 200:
                        with open(tmpPathZipFileName, 'wb') as f:
                            f.write(r.content)
                    worsica_logger.info('[startTopographyGenerating _store_topography]: unzip ' + zipFileName)
                    zipF = zipfile.ZipFile(tmpPathZipFileName, 'r')
                    zipF.extractall('/tmp/')
                    output_path = '/tmp/' + WORKSPACE_SIMULATION_FOLDER  # gt folder will be get on _store_raster
                    wis = WATER_INDEX.split(',')
                    for wi in wis:
                        indexTypes = [[wi.upper(), 'Topography_' + wi.upper()], ]
                        for itype in indexTypes:
                            _store_raster(gT, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, 'Topography', False, False, itype, output_path, False, True)
                        indexTypes = [[wi.upper(), 'FloodFreqs_' + wi.upper()], ]
                        for itype in indexTypes:
                            _store_raster(gT, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, 'FloodFreqs', False, False, itype, output_path, False, False, True)
                    views._change_generate_topo_state(gT, 'stored-generated-topo')
                    views._change_generate_topo_state(gT, 'success')
                except Exception as e:
                    print(traceback.format_exc())
                    views._change_generate_topo_state(gT, 'error-storing-generated-topo')
        except Exception as e:
            print(traceback.format_exc())
            worsica_logger.info('[startTopographyGenerating]: GenerateTopographyMap ' + str(GENERATE_TOPO_ID) + ' error: ' + str(e))
            views._change_generate_topo_state(gT, 'error-generating-topo')
    else:
        worsica_logger.info('[startTopographyGenerating]: Topography generation only available on coastal service and only if job submission was successful. Skip.')
