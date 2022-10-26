from __future__ import absolute_import, unicode_literals
from worsica_web_intermediate.celery import app

from . import views, processing_cleanup, subtasks, utils

from celery.schedules import crontab


@app.task
def taskStartProcessing(roi_id, job_submission_id, host):
    views.startProcessing(roi_id, job_submission_id, host)


@app.task
def taskStartProcessingDownload(PIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, ESA_USER_ID, IS_USERCHOSEN_IMAGE, host):
    subtasks._download_imageset(PIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, ESA_USER_ID, IS_USERCHOSEN_IMAGE, host)


@app.task
def taskStartProcessingL2AConversion(PIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, IS_USERCHOSEN_IMAGE, host):
    subtasks._convert_l2a_imageset(PIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, IS_USERCHOSEN_IMAGE, host)


@app.task
def taskStartProcessingResampling(PIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, GEOJSON_POLYGON, LOGS_FOLDER, LOG_FILENAME, IS_USERCHOSEN_IMAGE, host):
    subtasks._resample_imageset(PIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, GEOJSON_POLYGON, LOGS_FOLDER, LOG_FILENAME, IS_USERCHOSEN_IMAGE, host)


@app.task
def taskStartProcessingMerge(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, IS_USERCHOSEN_IMAGE, host):
    subtasks._merge_imagesets(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, IS_USERCHOSEN_IMAGE, host)
# ---water leak only ph1---


@app.task
def taskStartGeneratingVirtualImagesProcess(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, SAMPLE_IMAGESET_NAME, SIMULATION_NAME2, host):
    subtasks._generate_virtual_mergedimageset(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, SAMPLE_IMAGESET_NAME, SIMULATION_NAME2, host)


@app.task
def taskStartInterpolatingProductsProcess(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, host):
    subtasks._interpolation_mergedimageset(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, host)
# --------------------


@app.task
def taskStartProcessingProcess(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, BATH_VALUE,
                               TOPO_VALUE, WI_THRESHOLD, WATER_INDEX, LOGS_FOLDER, LOG_FILENAME, IS_USERCHOSEN_IMAGE, host):
    subtasks._process_mergedimageset(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, BATH_VALUE,
                                     TOPO_VALUE, WI_THRESHOLD, WATER_INDEX, LOGS_FOLDER, LOG_FILENAME, IS_USERCHOSEN_IMAGE, host)
# ---water leak only ph1---


@app.task
def taskStartGeneratingClimatologyProcess(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, WATER_INDEX, LOGS_FOLDER, LOG_FILENAME, host):
    subtasks._generate_climatology_avgmergedimageset(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, WATER_INDEX, LOGS_FOLDER, LOG_FILENAME, host)
# ---------------------


@app.task
def taskStartProcessingStore(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, WATER_INDEX, IS_CLIMATOLOGY, IS_USERCHOSEN_IMAGE, host):
    subtasks._store_mergedimageset(MPIS_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, WATER_INDEX, IS_CLIMATOLOGY, IS_USERCHOSEN_IMAGE, host)

# ---water leak only ph2---


@app.task
def taskStartProcessingLeakDetection(roi_id, job_submission_id, leak_detection_id, host):
    views.startProcessingLeakDetection(roi_id, job_submission_id, leak_detection_id, host)


@app.task
def taskStartProcessingLeakDetectionAnomaly(LEAK_DETECTION_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, host):
    subtasks._process_mergedimageset_leakdetection_anomaly(LEAK_DETECTION_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, host)


@app.task
def taskStartProcessingLeakDetectionSecondDeriv(LEAK_DETECTION_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, host):
    subtasks._process_mergedimageset_leakdetection_second_deriv(LEAK_DETECTION_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, host)


@app.task
def taskStartProcessingLeakDetection2(roi_id, job_submission_id, leak_detection_id, host):
    views.startProcessingLeakDetection2(roi_id, job_submission_id, leak_detection_id, host)


@app.task
def taskStartProcessingLeakDetectionGenerateMask(LEAK_DETECTION_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, host):
    subtasks._process_mergedimageset_leakdetection_filterleaks(LEAK_DETECTION_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, host)


@app.task
def taskStartProcessingLeakDetectionIdentifyLeaks(LEAK_DETECTION_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, host):
    subtasks._process_mergedimageset_leakdetection_identifyleaks(LEAK_DETECTION_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, LOGS_FOLDER, LOG_FILENAME, host)


@app.task
def taskStopProcessingLeakDetection(job_submission_id, leak_detection_id, host):
    views.stopProcessingLeakDetection(job_submission_id, leak_detection_id, host)
# ---------------------


@app.task
def taskRestartProcessing(roi_id, job_submission_id, host):
    views.restartProcessing(roi_id, job_submission_id, host)


@app.task
def taskUpdateProcessing(roi_id, job_submission_id, host):
    views.updateProcessing(roi_id, job_submission_id, host)


@app.task
def taskStopProcessing(roi_id, job_submission_id, host):
    views.stopProcessing(roi_id, job_submission_id, host)


@app.task
def taskDeleteProcessing(roi_id, job_submission_id, host):
    views.deleteProcessing(roi_id, job_submission_id, host)


@app.task
def taskSubmitProcessingDataverse(roi_id, job_submission_id, jsonReq, host):
    views.submitProcessingDataverse(roi_id, job_submission_id, jsonReq, host)

#


@app.task
def taskStartTopography(roi_id, job_submission_id, generate_topo_id, jsonReq, host):
    views.startTopographyProcessing(roi_id, job_submission_id, generate_topo_id, jsonReq, host)


@app.task
def taskStartTopographyGenerating(GENERATE_TOPO_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, ZREF, COORDS, TIDE_FILENAME, WATER_INDEX, LOGS_FOLDER, LOG_FILENAME, host):
    subtasks._generate_topography(GENERATE_TOPO_ID, SERVICE, USER_ID, ROI_ID, SIMULATION_ID, PREV_JOB_SUBMISSION_STATE, ZREF, COORDS, TIDE_FILENAME, WATER_INDEX, LOGS_FOLDER, LOG_FILENAME, host)


@app.task
def taskStartCloneCleanup(job_submission_id, process_imageset_id, actual_shapeline_group_id, cloned_shapeline_group_id, remove_outside_polygon, jsonReq, host):
    views.startCloneCleanupProcessing(job_submission_id, process_imageset_id, actual_shapeline_group_id, cloned_shapeline_group_id, remove_outside_polygon, jsonReq, host)


@app.task
def taskStartCreateThreshold(job_submission_id, process_imageset_id, raster_type, created_threshold_id, rm_created_threshold_id, jsonReq, host):
    views.startCreateThreshold(job_submission_id, process_imageset_id, raster_type, created_threshold_id, rm_created_threshold_id, jsonReq, host)


@app.task
def taskStartCleanup():
    processing_cleanup.check()


@app.task
def taskStartEmptyingTrash():
    processing_cleanup.cleanup_trash()


@app.task
def taskSendNotificationEmailProcessingJobSubmissions():
    utils.notify_ongoing_job_submission_state()


app.conf.beat_schedule = {
    'start_cleanup': {
        'task': 'worsica_api.tasks.taskStartCleanup',
        'schedule': crontab(hour="0", minute="0"),
    },
    'start_emptying_trash': {
        'task': 'worsica_api.tasks.taskStartEmptyingTrash',
        'schedule': crontab(hour="3", minute="0"),
    },
    'start_notifying_running_jobs': {
        'task': 'worsica_api.tasks.taskSendNotificationEmailProcessingJobSubmissions',
        'schedule': crontab(hour="1", minute="0"),
    }
}
