import worsica_api.models as worsica_api_models
from worsica_web_intermediate import nextcloud_access

from datetime import datetime
import pytz
import requests


def check():
    utc = pytz.utc
    datetime_now = utc.localize(datetime.now())
    diss = worsica_api_models.RepositoryImageSets.objects.all()
    print('------------------------')
    print('Date-time: ' + str(datetime_now))
    print('Start check')
    isSuccess = True
    for dis in diss:
        print(dis.name)
        expDate = dis.expirationDate
        if expDate <= datetime_now:
            print('- Expired at ' + str(expDate))
            if dis.state != 'expired':
                try:
                    r = requests.delete(dis.filePath, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
                    if (r.status_code in [204, 404]):
                        dis.state = 'expired'
                        dis.managedByJobId = None
                        dis.save()
                        print(str(dis.filePath) + " was set to expired")
                    else:
                        print("Error: " + str(dis.filePath) + " can not be removed (code " + str(r.status_code) + ")")
                        isSuccess = False
                except Exception as error:
                    print("Error: " + error)
                    isSuccess = False
            else:
                print(str(dis.filePath) + " has already expired, do nothing")
        else:
            print('- Still valid')
    if isSuccess:
        print('Finished successfully')
        print('------------------------')
        return 0
    else:
        print('Finished with errors')
        print('------------------------')
        return 1


def cleanup_trash():
    utc = pytz.utc
    datetime_now = utc.localize(datetime.now())
    print('------------------------')
    print('Date-time: ' + str(datetime_now))
    print('Start cleanup trash')
    isSuccess = True
    try:
        urlPath = nextcloud_access.NEXTCLOUD_URL_PATH
        trashPath = urlPath.replace('files', 'trashbin') + '/trash'
        print(trashPath)
        r = requests.delete(trashPath, auth=(nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD))
        if (r.status_code == 204):
            print("Trash is now empty")
        else:
            print("Error: cannot cleanup trash (code " + str(r.status_code) + ")")
            isSuccess = False
    except Exception as error:
        print("Error: " + error)
        isSuccess = False
    if isSuccess:
        print('Finished successfully')
        print('------------------------')
        return 0
    else:
        print('Finished with errors')
        print('------------------------')
        return 1
