import json
import os
import time
import traceback
import requests
import datetime

from worsica_web_intermediate import dataverse_access

from django.contrib.gis.geos import GEOSGeometry


def create_dataverse(base_url, api_token, j, name='root'):
    try:
        post_url = base_url + "/api/dataverses/" + name
        print('[create_dataverse] Start creating dataverse ' + j['name'] + ' from ' + post_url)
        resp = requests.post(post_url,
                             headers={'X-Dataverse-key': api_token},
                             data=json.dumps(j))
        if resp.status_code in [404, 500]:
            raise Exception('[create_dataverse] ' + resp.json()['message'])
        time.sleep(1)
        print('[create_dataverse] Success! Dataverse created!')
        return resp
    except Exception as e:
        print(traceback.format_exc())
        raise Exception('[create_dataverse] Error on create dataset: ' + str(e))


def create_dataverse_user(BASE_URL, API_TOKEN, DV_MAIN, service, user_id, roi_id, simulation_id, owner):
    try:
        print('[create_dataverse_user] Check and create datasets (if needed)')
        # /worsica/[public|private]/[service]/[user]/[roi]/[simulation?]
        # build a descending tree
        dictServiceNames = {'coastal': 'Coastal', 'inland': 'Inland', 'waterleak': 'Water leak'}
        DATAVERSE_TREE = [
            ['worsica', generate_json_dataverse(DV_MAIN, DV_MAIN, 'worsica@lnec.pt')],
            [DV_MAIN, generate_json_dataverse(dictServiceNames[service], DV_MAIN + '-' + service, 'worsica@lnec.pt')],
            [DV_MAIN + '-' + service, generate_json_dataverse('User ' + str(user_id), DV_MAIN + '-' + service + '-user' + str(user_id), owner['email'], owner['affiliation'], "RESEARCHERS")],
            [DV_MAIN + '-' + service + '-user' + str(user_id), generate_json_dataverse('ROI ' + str(roi_id), DV_MAIN + '-' + service + '-user' +
                                                                                       str(user_id) + '-roi' + str(roi_id), owner['email'], owner['affiliation'], "RESEARCHERS")],
            [DV_MAIN +
             '-' +
             service +
             '-user' +
             str(user_id) +
                '-roi' +
                str(roi_id), generate_json_dataverse('Simulation ' +
                                                     str(simulation_id), DV_MAIN +
                                                     '-' +
                                                     service +
                                                     '-user' +
                                                     str(user_id) +
                                                     '-roi' +
                                                     str(roi_id) +
                                                     '-simulation' +
                                                     str(simulation_id), owner['email'], owner['affiliation'], "RESEARCH_PROJECTS")],
            [DV_MAIN + '-' + service + '-user' + str(user_id) + '-roi' + str(roi_id) + '-simulation' + str(simulation_id), None]
        ]
        # start creating the user below
        for dt in DATAVERSE_TREE:
            if dt[1] is not None:  # if has json for dataverse, create, else skip
                create_dataverse(BASE_URL, API_TOKEN, dt[1], dt[0])
            # always publish the dataverse to be able publish the dataset
            publish_dataverse(BASE_URL, API_TOKEN, dt[0])
        print(DATAVERSE_TREE[-1][0])
        return DATAVERSE_TREE[-1][0]
    except Exception as e:
        print(traceback.format_exc())
        raise Exception('[create_dataverse_user] ' + str(e))


def generate_json_dataverse(name, alias, email, affiliation='LNEC', dataverseType="LABORATORY"):
    dv = {
        "name": name,
        "alias": alias,
        "dataverseContacts": [{"contactEmail": email}],
        "affiliation": affiliation,
        "dataverseType": dataverseType
    }
    return dv


def publish_dataverse(base_url, api_token, _id):
    try:
        resp = requests.post(base_url + "/api/dataverses/" + _id + "/actions/:publish",
                             headers={'X-Dataverse-key': api_token})

        if resp.status_code in [404, 500]:
            raise Exception(resp.json()['message'])
        time.sleep(1)
        print('[publish_dataverse] Success! Dataverse published!')
        return resp
    except Exception as e:
        print(traceback.format_exc())
        raise Exception('[publish_dataverse] Error on publishing dataverse: ' + str(e))


def create_dataset(base_url, api_token, ds, dv_alias):
    try:
        resp = requests.post(base_url + "/api/dataverses/" + dv_alias + "/datasets",
                             headers={'X-Dataverse-key': api_token},
                             data=ds)

        if resp.status_code in [404, 500]:
            raise Exception(resp.json()['message'])
        pid = resp.json()['data']['persistentId']
        time.sleep(1)
        print('[create_dataset] Success! Dataset created!')
        return resp, pid
    except Exception as e:
        print(traceback.format_exc())
        raise Exception('[create_dataset] Error on create dataset: ' + str(e))


def delete_dataset(base_url, api_token, persistentId):
    # unpublished: curl -H "X-Dataverse-key: $API_TOKEN" -X DELETE $SERVER_URL/api/datasets/$ID
    # published: curl -H "X-Dataverse-key: $API_TOKEN" -X DELETE $SERVER_URL/api/datasets/:persistentId/destroy/?persistentId=$PERSISTENT_ID
    try:
        resp = requests.delete(base_url + "/api/datasets/:persistentId/destroy?persistentId=" + persistentId,
                               headers={'X-Dataverse-key': api_token})

        if resp.status_code in [404, 500]:  # json()['status']=='ERROR':
            raise Exception(resp.json()['message'])
        time.sleep(1)
        print('[delete_dataset] Success! Dataset deleted!')
        return resp
    except Exception as e:
        print(traceback.format_exc())
        raise Exception('[delete_dataset] Error on deleting dataset: ' + str(e))


def publish_dataset(base_url, api_token, persistentId):
    # curl -H X-Dataverse-key:$API_TOKEN -X POST $SERVER_URL/api/datasets/$ID/actions/:publish
    try:
        resp = requests.post(base_url + "/api/datasets/:persistentId/actions/:publish?persistentId=" + persistentId + '&type=major',
                             headers={'X-Dataverse-key': api_token})

        if resp.status_code in [404, 500]:  # json()['status']=='ERROR':
            raise Exception(resp.json()['message'])
        time.sleep(1)
        print('[publish_dataset] Success! Dataverse published!')
        return resp
    except Exception as e:
        print(traceback.format_exc())
        raise Exception('[publish_dataset] Error on publishing dataverse: ' + str(e))


def get_software_version_by_env(env_var, default_version_number):
    # env_var: key to find on os.environ, e.g: PYTHON_VERSION
    # default_version_number: if env_var not found, throw a version number by default
    if env_var in os.environ:
        return str(os.environ[env_var].split('#')[0])  # remove any comments trailing after the variable
    else:
        return default_version_number


def generate_json_dataset(job_submission, piss, mpiss, ampiss, gts, otherData):
    job_submission_exec = json.loads(job_submission.exec_arguments)
    SERVICE = job_submission.service
    SERVICE_NAME = 'Unknown'
    if SERVICE == 'coastal':
        SERVICE_NAME = 'Coastal'
        BATH_VALUE = str(job_submission_exec['detection']['bathDepth'])
        TOPO_VALUE = str(job_submission_exec['detection']['topoHeight'])
    elif SERVICE == 'inland':
        SERVICE_NAME = 'Inland'
    if SERVICE == 'waterleak':
        SERVICE_NAME = 'Water Leak'
    WKT_POLYGON = job_submission_exec['roi']
    JOB_SUBMISSION_NAME = job_submission.name
    ROI_ID = job_submission.aos_id
    USER_ID = job_submission.user_id
    SIMULATION_ID = job_submission.simulation_id
    PROVIDER = job_submission.provider
    polygon = GEOSGeometry(str(WKT_POLYGON))
    polygon_extent = polygon.extent

    dataInputs = []
    if ('sentinel' in PROVIDER):
        for pis in piss:
            dataInputs.append(pis.name + " (ESA UUID: " + pis.uuid + ")")
    else:
        dataInputs.append("[Confidential. For private inputs, please ask the owner to provide information about them.]")

    dataOutputs = []
    for mpis in mpiss:
        IMAGESET_NAME = mpis.name
        WORKSPACE_SIMULATION_FOLDER = 'portal/rois/' + str(ROI_ID) + '/simulations/' + str(SIMULATION_ID) + '/intermediate/processimageset/' + str(mpis.id) + '/download_products'
        dataOutputs.append(IMAGESET_NAME + " (URL: " + dataverse_access.DATAVERSE_PATH_TO_WORSICA_FRONTEND + '/' + WORKSPACE_SIMULATION_FOLDER + ")")
    for ampis in ampiss:
        IMAGESET_NAME = ampis.name
        WORKSPACE_SIMULATION_FOLDER = 'portal/rois/' + str(ROI_ID) + '/simulations/' + str(SIMULATION_ID) + '/intermediate/averageprocessimageset/' + str(ampis.id) + '/download_products'
        dataOutputs.append(IMAGESET_NAME + " (URL: " + dataverse_access.DATAVERSE_PATH_TO_WORSICA_FRONTEND + '/' + WORKSPACE_SIMULATION_FOLDER + ")")
    for gt in gts:
        IMAGESET_NAME = gt.name
        WORKSPACE_SIMULATION_FOLDER = 'portal/rois/' + str(ROI_ID) + '/simulations/' + str(SIMULATION_ID) + '/intermediate/generatetopography/' + str(gt.id) + '/download_products'
        dataOutputs.append(IMAGESET_NAME + " (URL: " + dataverse_access.DATAVERSE_PATH_TO_WORSICA_FRONTEND + '/' + WORKSPACE_SIMULATION_FOLDER + ")")

    software = [
        ['Python', get_software_version_by_env('PYTHON_VERSION', '3.6.3')],
        ['gdal', get_software_version_by_env('GDAL_VERSION', '3.0.4')],
        ['opencv-python', get_software_version_by_env('OPENCV_VERSION', '4.0.0.21')],
        ['numpy', get_software_version_by_env('NUMPY_VERSION', '1.15.2')],
        ['scikit-image', get_software_version_by_env('SCIKIT_IMAGE_VERSION', '0.17.2')],
        ['sentinelsat', get_software_version_by_env('SENTINELSAT_VERSION', '0.13')],
        ['google-cloud-storage', get_software_version_by_env('GCS_VERSION', '1.37.1')],
        ['dea-tools', get_software_version_by_env('DEA_TOOLS_VERSION', '0.2.5')],
    ]

    owner = otherData['owner']
    title = otherData['title']

    description = 'Service: ' + str(SERVICE_NAME) + "; Simulation: " + str(JOB_SUBMISSION_NAME) + "; ROI polygon: " + str(polygon_extent) + "."
    productionDate = str(datetime.datetime.now().strftime('%Y-%m-%d'))
    print(productionDate)
    dic = {
        'citation': {
            'citation_title': title,
            'citation_productionDate': productionDate,
            'citation_accessToSources': 'Unknown',
            'citation_author': [
                [owner['name'], owner['affiliation']]
            ],
            'citation_datasetContact': [
                [owner['name'], owner['email']]
            ],
            'citation_dsDescriptionValue': description,
            'citation_subject': [
                "Computer and Information Science",
                "Earth and Environmental Sciences"
            ],
            'citation_displayName': title + " Citation Metadata",
            'citation_dataSources': dataInputs,
            'citation_otherReferences': dataOutputs,
            'citation_software': software,
        },
        'geospatial': {
            'geospatial_country': "Portugal",
            'geospatial_state': "Lisbon",
            'geospatial_city': "Lisbon",
            'geospatial_otherGeographicCoverage': '\'' + WKT_POLYGON + '\'',
            'geospatial_geographicUnit': ["degrees", "degrees"],
            'geospatial_westLongitude': str(polygon_extent[0]),
            'geospatial_eastLongitude': str(polygon_extent[2]),
            'geospatial_northLongitude': str(polygon_extent[1]),
            'geospatial_southLongitude': str(polygon_extent[3]),
            'geospatial_displayName': title + " Geographic Metadata"
        }
    }
    return dic


def generate_demo_json_dataset():
    dic = {
        'citation': {
            'citation_title': "Worsica Test",
            'citation_productionDate': "2022-06-07",
            'citation_accessToSources': 'Unknown',
            'citation_author': [
                ["Ricardo Martins", "LNEC"],
                ["Alberto Azevedo", "LNEC"]
            ],
            'citation_datasetContact': [
                ["Ricardo Martins", "rjmartins@lnec.pt"],
                ["Alberto Azevedo", "aazevedo@lnec.pt"]
            ],
            'citation_dsDescriptionValue': "Test sample",
            'citation_subject': ["Computer and Information Science", "Earth and Environmental Sciences"],
            'citation_displayName': "Worsica Citation Metadata",
            'citation_dataSources': ['SourceA', 'SourceB'],
            'citation_otherReferences': ['RefA', 'RefB'],
            'citation_software': [
                ['GDAL', '3.0.4'],
                ['OpenCV', '4.0.0.21']
            ]
        },
        'geospatial': {
            'geospatial_country': "Portugal",
            'geospatial_state': "Lisbon",
            'geospatial_city': "Lisbon",
            'geospatial_otherGeographicCoverage': "Av.Brasil",
            'geospatial_geographicUnit': ["m", "m"],
            'geospatial_westLongitude': "-9",
            'geospatial_eastLongitude': "-8",
            'geospatial_northLongitude': "38",
            'geospatial_southLongitude': "37",
            'geospatial_displayName': "Worsica Geographic Metadata"
        }
    }
    return dic


def upload_datafile(base_url, api_token, pid, filename, df):
    try:
        resp = requests.post(base_url + '/api/datasets/:persistentId/add?persistentId={0}'.format(pid),
                             headers={'X-Dataverse-key': api_token},
                             data={'jsonData': df},
                             files={'file': open(filename, 'rb')},
                             )
        print(resp.status_code)
        print(resp.url)
        print(resp.content)
        print('Success! Datafile created!')
        return resp
    except Exception as e:
        traceback.print_exc()
        print('Error on upload datafile: ' + str(e))
        return None


def generate_json_datafile(job_submission, filepath):
    ROI_ID = job_submission.aos_id
    SIMULATION_ID = job_submission.simulation_id
    dict_json_datafiles_upload = {
        filepath:
        {
            "description": "ROI" + str(ROI_ID) + "-Simulation" + str(SIMULATION_ID) + " Final products",
            "categories": ["Data"],
            "directoryLabel": "data/subdir1",
            "restrict": False
        }
    }
    return dict_json_datafiles_upload


def generate_demo_json_datafile():
    dict_json_datafiles_upload = {
        os.getcwd() + "/dataverse/worsica_dv_ds_upload_template.json":
        {
            "description": "Test template Dataset",
            "categories": ["Data"],
            "directoryLabel": "data/subdir1",
            "restrict": False
        }
    }
    return dict_json_datafiles_upload


def fill_dataset_template(dic, ds):
    nds = ds.copy()
    nds_metablocks = nds['datasetVersion']['metadataBlocks']
    nds_citation = nds_metablocks['citation']
    nds_citation['displayName'] = dic['citation']['citation_displayName']
    for f in nds_citation['fields']:
        if 'title' in f['typeName']:
            f['value'] = dic['citation']['citation_title']
        if 'productionDate' in f['typeName']:
            f['value'] = dic['citation']['citation_productionDate']
        if 'accessToSources' in f['typeName']:
            f['value'] = dic['citation']['citation_accessToSources']
        if 'author' in f['typeName']:
            f['value'] = [{
                "authorName": {
                    "value": str(x[0]), "typeClass": "primitive",
                    "multiple": False, "typeName": "authorName"
                },
                "authorAffiliation": {
                    "value": str(x[1]), "typeClass": "primitive",
                    "multiple": False, "typeName": "authorAffiliation"
                }
            } for x in dic['citation']['citation_author']]
        if 'datasetContact' in f['typeName']:
            f['value'] = [{
                "datasetContactName": {
                    "typeClass": "primitive", "multiple": False,
                    "typeName": "datasetContactName", "value": str(x[0])
                },
                "datasetContactEmail": {
                    "typeClass": "primitive", "multiple": False,
                    "typeName": "datasetContactEmail", "value": str(x[1])
                }
            } for x in dic['citation']['citation_datasetContact']]
        if 'dsDescription' in f['typeName']:
            f['value'][0]['dsDescriptionValue']['value'] = dic['citation']['citation_dsDescriptionValue']
        if 'subject' in f['typeName']:
            f['value'] = dic['citation']['citation_subject']
        if 'dataSources' in f['typeName']:
            f['value'] = dic['citation']['citation_dataSources']
        if 'otherReferences' in f['typeName']:
            f['value'] = dic['citation']['citation_otherReferences']
        if 'software' in f['typeName']:
            f['value'] = [{
                "softwareName": {
                    "typeName": "softwareName", "multiple": False,
                                "typeClass": "primitive", "value": str(x[0])
                },
                "softwareVersion": {
                    "typeName": "softwareVersion", "multiple": False,
                                "typeClass": "primitive", "value": str(x[1])
                }
            } for x in dic['citation']['citation_software']]
    nds_geospatial = nds_metablocks['geospatial']
    nds_geospatial['displayName'] = dic['geospatial']['geospatial_displayName']
    for f in nds_geospatial['fields']:
        if 'geographicCoverage' in f['typeName']:
            f['value'][0]['country']['value'] = dic['geospatial']['geospatial_country']
            f['value'][0]['state']['value'] = dic['geospatial']['geospatial_state']
            f['value'][0]['city']['value'] = dic['geospatial']['geospatial_city']
            f['value'][0]['otherGeographicCoverage']['value'] = dic['geospatial']['geospatial_otherGeographicCoverage']
        if 'geographicUnit' in f['typeName']:
            f['value'] = dic['geospatial']['geospatial_geographicUnit']
        if 'geographicBoundingBox' in f['typeName']:
            f['value'][0]['westLongitude']['value'] = dic['geospatial']['geospatial_westLongitude']
            f['value'][0]['eastLongitude']['value'] = dic['geospatial']['geospatial_eastLongitude']
            f['value'][0]['northLongitude']['value'] = dic['geospatial']['geospatial_northLongitude']
            f['value'][0]['southLongitude']['value'] = dic['geospatial']['geospatial_southLongitude']
    return nds


def dataverse_upload(BASE_URL, DV_ALIAS, API_TOKEN, gen_json_dataset):
    try:
        if (BASE_URL is None):
            raise Exception('No Dataverse URL, please verify URL.')
        elif (DV_ALIAS is None):
            raise Exception('No Dataverse alias, please create a Dataverse before uploading datasets.')
        elif (API_TOKEN is None):
            raise Exception('Error: No Dataverse API token, please go to your account and generate API key.')
        if (gen_json_dataset is None):
            raise Exception('Error: No json dataset.')
        else:
            print('[dataverse_upload] Load dv upload json template to ' + BASE_URL + '.')
            # datasets
            dv_ds_template = json.load(open(os.getcwd() + '/dataverse/worsica_dv_ds_upload_template.json'))
            dict_json_dataset_upload = fill_dataset_template(gen_json_dataset, dv_ds_template)

            print('[dataverse_upload] Upload dataset')
            resp, pid = create_dataset(BASE_URL, API_TOKEN, json.dumps(dict_json_dataset_upload), DV_ALIAS)
            resp2 = publish_dataset(BASE_URL, API_TOKEN, pid)
            return {'state': 'success', 'msg': 'Uploaded successfuly!', 'resp': resp.json(), 'pid': pid}

    except Exception as e:
        print(traceback.format_exc())
        msg = '[dataverse_upload] An error has occurred: ' + str(e)
        raise Exception(msg)


def test_dataverse_upload():
    BASE_URL = dataverse_access.DATAVERSE_BASE_URL
    DV_ALIAS = dataverse_access.DATAVERSE_ALIAS
    API_TOKEN = dataverse_access.DATAVERSE_API_TOKEN
    gen_json_dataset = generate_demo_json_dataset()
    gen_json_datafile = generate_demo_json_datafile()
    dataverse_upload(BASE_URL, DV_ALIAS, API_TOKEN, gen_json_dataset, gen_json_datafile)
