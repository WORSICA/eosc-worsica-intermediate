from worsica_web_intermediate import settings
import worsica_api.models as worsica_api_models
from django.core.mail import send_mail
import json
import shlex


def _build_command_shell_to_run(SCRIPT_FILE, LOG_FILEPATH=None, ENABLE_GRID=False):
    print('_build_command_shell_to_run')
    if LOG_FILEPATH:
        print('with logs to ' + LOG_FILEPATH)
        if ENABLE_GRID:
            print('---> Run on the grid')
            SUBMISSION_NAME = LOG_FILEPATH.split('/')[-1].replace('.out', '')  # submission name is log filename without extension
            #cs = 'unbuffer ./worsica_grid_launch_submission.sh ' + SUBMISSION_NAME + ' \"' + SCRIPT_FILE + '\" ' + LOG_FILEPATH + ' | tee ' + LOG_FILEPATH + '.debug'  # generate debug log too
            cs = ['/bin/sh','-c','unbuffer ./worsica_grid_launch_submission.sh ' + SUBMISSION_NAME + ' \'[' + SCRIPT_FILE + ']\' ' + LOG_FILEPATH + ' | tee ' + LOG_FILEPATH + '.debug']
        else:
            print('---> Run locally')
            cs = 'unbuffer ' + SCRIPT_FILE + ' | tee ' + LOG_FILEPATH
            cs = shlex.split("/bin/sh -c '" + cs + "'")
    else:
        print('without logs')
        print('---> Run locally')
        cs = 'unbuffer ' + SCRIPT_FILE
        cs = shlex.split("/bin/sh -c '" + cs + "'")
    return cs


# Send email notifications
def notify_users(send_to, subject, plain_content):
    # send_to: array of emails
    # subject: email subject
    # dic_pars: passes dictionary to template
    # plain_template: link to plain text template
    # html_template: link to html template
    try:
        print('debug')
        print('[WORSICA] ' + subject)
        print(plain_content)
        print(settings.WORSICA_DEFAULT_EMAIL)
        print(send_to)
        s = send_mail(
            '[WORSICA] ' + subject,
            plain_content,
            settings.WORSICA_DEFAULT_EMAIL,
            send_to,
            fail_silently=False
        )
        print(s)
    except Exception as e:
        print('Error sending email: ' + str(e))

# Send email


def notify_ongoing_job_submission_state():
    jss = worsica_api_models.JobSubmission.objects.all()
    for job_submission in jss:
        print(job_submission.state)
        isNotRunning = ('error' in job_submission.state) or ('success' in job_submission.state) or ('submitted' in job_submission.state)
        if not isNotRunning:  # running
            print('Is running, send email')
            notify_processing_state(job_submission)

# Send email notification to User


def notify_processing_state(job_submission):
    # Prepare email parameters
    proc_obj = json.loads(job_submission.obj)
    if 'user_email' in proc_obj['areaOfStudy']:  # if user email exists, send email
        notify_processing_state_email(job_submission, proc_obj['areaOfStudy']['user_email'])
    else:
        print('Error: no user_email associated to this job submission, unable to send email!')


def notify_processing_state_email(job_submission, new_user_email):
    proc_name = job_submission.name
    proc_serv = job_submission.service  # get_service_display()
    subject = 'Processing report of ' + proc_name + ' (' + proc_serv + ')'
    if 'error' in job_submission.state:
        subject += ' - FAILED'
    elif 'success' in job_submission.state:
        subject += ' - FINISHED'
    elif 'submitted' in job_submission.state:
        subject += ' - SUBMITTED'
    else:
        subject += ' - RUNNING'
    plain_content_js = (
        "--------------Job Submission Details-----------------\n" +
        "Name: " + proc_name + "\n" +
        "Subservice: " + job_submission.get_service_display() + "\n" +
        "State: " + job_submission.get_state_display() + "\n" +
        "Submitted at: " + str(job_submission.lastSubmittedAt) + "\n" +
        "Run at: " + str(job_submission.lastRunAt) + "\n" +
        "Finished at: " + str(job_submission.lastFinishedAt) + "\n")
    plain_content_pis = "---------------Processed Imagesets----------------\n"
    for pis in worsica_api_models.ProcessImageSet.objects.filter(jobSubmission=job_submission).order_by('id'):
        plain_content_pis += pis.small_name + ": " + pis.get_processState_display() + "\n"
    plain_content_mpis = "---------------Merged Imagesets----------------\n"
    for mpis in worsica_api_models.MergedProcessImageSet.objects.filter(jobSubmission=job_submission).order_by('-sensingDate'):
        plain_content_mpis += mpis.small_name + ": " + mpis.get_processState_display() + "\n"
    plain_content_ampis = ""
    if (proc_serv == 'waterleak'):
        plain_content_ampis = "---------------Climatologies----------------\n"
        for ampis in worsica_api_models.AverageMergedProcessImageSet.objects.filter(jobSubmission=job_submission).order_by('-date'):
            plain_content_ampis += ampis.small_name + ": " + ampis.get_processState_display() + "\n"
    plain_content = ('Hello\n' +
                     "This is an automatic email reporting the WORSICA processing status of " + proc_name + " (" + proc_serv + ").\n" +
                     "Please check details of this processing on WORSICA portal under " + job_submission.get_service_display() + " subservice.\n"
                     "\n" +
                     plain_content_js +
                     "\n" +
                     plain_content_pis +
                     "\n" +
                     plain_content_mpis +
                     "\n" +
                     plain_content_ampis +
                     "\n" +
                     "For further enquiries, please contact us at worsica(at)lnec.pt.\n"
                     )
    # Send email to user
    notify_users([new_user_email], subject, plain_content)

# Send email notification to User


def notify_dataverse_upload_state(job_submission, DATAVERSE_BASE_URL):
    # Prepare email parameters
    proc_obj = json.loads(job_submission.obj)
    if 'user_email' in proc_obj['areaOfStudy']:  # if user email exists, send email
        notify_dataverse_upload_state_email(job_submission, proc_obj['areaOfStudy']['user_email'], DATAVERSE_BASE_URL)
    else:
        print('Error: no user_email associated to this job submission, unable to send email!')


def notify_dataverse_upload_state_email(job_submission, new_user_email, DATAVERSE_BASE_URL):
    proc_name = job_submission.name
    proc_serv = job_submission.service  # get_service_display()
    subject = 'Dataverse submission report of ' + proc_name + ' (' + proc_serv + ')'
    job_submission_dataverse = worsica_api_models.DataverseSubmission.objects.get(jobSubmission=job_submission)
    plain_content_js_doi = ""
    plain_content_js_link = ""
    if 'error-dataverse-uploading' in job_submission_dataverse.state:
        subject += ' - FAILED'
    elif 'dataverse-uploaded' in job_submission_dataverse.state:
        subject += ' - FINISHED'
        plain_content_js_doi = "DOI: " + str(job_submission_dataverse.doi) + "\n"
        plain_content_js_link = "Link: " + DATAVERSE_BASE_URL + "/dataset.xhtml?persistentId=" + str(job_submission_dataverse.doi) + " \n"
    elif 'submitted' in job_submission_dataverse.state:
        subject += ' - SUBMITTED'
    else:
        subject += ' - RUNNING'
    plain_content_js = (
        "--------------Job submission Details-----------------\n" +
        "Name: " + proc_name + "\n" +
        "Subservice: " + job_submission.get_service_display() + "\n" +
        "--------------Dataverse submission Details-----------------\n" +
        "Title: " + str(job_submission_dataverse.name) + "\n" +
        "State: " + job_submission_dataverse.get_state_display() + "\n" +
        "Submitted at: " + str(job_submission_dataverse.creationDate) + "\n" +
        plain_content_js_doi +
        plain_content_js_link)

    plain_content = ('Hello\n' +
                     "This is an automatic email reporting the WORSICA dataverse submission status of " + proc_name + " (" + proc_serv + ").\n" +
                     "\n" + plain_content_js + "\n" +
                     "For further enquiries, please contact us at worsica(at)lnec.pt.\n"
                     )
    # Send email to user
    notify_users([new_user_email], subject, plain_content)

# Send email notification to User


def notify_leakdetection_processing_state(mpisld, ue=None):
    # Prepare email parameters
    job_submission = mpisld.jobSubmission
    proc_obj = json.loads(job_submission.obj)
    if ue is None and 'user_email' in proc_obj['areaOfStudy']:  # if user email exists, send email
        ue = proc_obj['areaOfStudy']['user_email']
        notify_leakdetection_processing_state_email(mpisld, ue)
    elif ue is not None:
        notify_leakdetection_processing_state_email(mpisld, ue)
    else:
        print('Error: no user_email associated to this job submission, unable to send email!')


def notify_leakdetection_processing_state_email(leak_detection, new_user_email):
    proc_name = leak_detection.name
    proc_serv = 'Leak Detection'
    subject = 'Processing report of ' + proc_name + ' (' + proc_serv + ')'
    if 'error' in leak_detection.processState:
        subject += ' - FAILED'
    elif 'submitted' in leak_detection.processState:
        subject += ' - SUBMITTED'
    elif 'stored-determinated-leak' in leak_detection.processState:
        subject += ' - IDENTIFIED LEAK POINTS'
    elif 'stored-calculated-leak' in leak_detection.processState:
        subject += ' - CALCULATED 2ND DERIVATIVE'
    else:
        subject += ' - RUNNING'
    plain_content_js = (
        "--------------Leak Detection Details-----------------\n" +
        "Name: " + proc_name + "\n" +
        "State: " + leak_detection.get_processState_display() + "\n" +
        "Index Type: " + str(leak_detection.indexType) + "\n" +
        "Image source from: " + str(leak_detection.image_source_from) + "\n" +
        "Calculate 2nd deriv: " + str(leak_detection.start_processing_second_deriv_from) + "\n"
    )
    plain_content_pis = "---------------Processed Imagesets----------------\n"
    plain_content_mpis = "---------------Merged Imagesets----------------\n"
    if leak_detection.image_source_from == 'new_image':
        user_chosen_mpis = leak_detection.ucprocessImageSet  # UserChosenMergedProcessImageSet
        print(user_chosen_mpis)
        if (user_chosen_mpis is not None):
            for mpis in worsica_api_models.UserChosenMergedProcessImageSet.objects.filter(id=user_chosen_mpis.id).order_by('-sensingDate'):
                plain_content_mpis += mpis.small_name + ": " + mpis.get_processState_display() + "\n"
            for pis in worsica_api_models.UserChosenProcessImageSet.objects.filter(id__in=user_chosen_mpis.procImageSet_ids.split(',')).order_by('id'):
                plain_content_pis += pis.small_name + ": " + pis.get_processState_display() + "\n"
        else:
            plain_content_pis += "(Unable to get list)\n"
            plain_content_mpis += "(Unable to get list)\n"
    elif leak_detection.image_source_from == 'processed_image':
        user_chosen_mpis = leak_detection.processImageSet  # UserChosenMergedProcessImageSet
        print(user_chosen_mpis)
        if (user_chosen_mpis is not None):
            for mpis in worsica_api_models.MergedProcessImageSet.objects.filter(id=user_chosen_mpis.id).order_by('-sensingDate'):
                plain_content_mpis += mpis.small_name + ": " + mpis.get_processState_display() + "\n"
            for pis in worsica_api_models.ProcessImageSet.objects.filter(id__in=user_chosen_mpis.procImageSet_ids.split(',')).order_by('id'):
                plain_content_pis += pis.small_name + ": " + pis.get_processState_display() + "\n"
        else:
            plain_content_pis += "(Unable to get list)\n"
            plain_content_mpis += "(Unable to get list)\n"

    plain_content = ('Hello\n' +
                     "This is an automatic email reporting the WORSICA processing status of " + proc_name + " (" + proc_serv + ").\n" +
                     "Please check details of this processing on WORSICA portal under Water leak > Leak Detection.\n"
                     "\n" +
                     plain_content_js +
                     "\n" +
                     plain_content_pis +
                     "\n" +
                     plain_content_mpis +
                     "\n" +
                     "For further enquiries, please contact us at worsica(at)lnec.pt.\n"
                     )
    # Send email to user
    notify_users([new_user_email], subject, plain_content)
