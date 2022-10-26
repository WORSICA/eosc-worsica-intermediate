from . import models_base
from datetime import datetime, timedelta
from django.db import models
import raster.models as raster_models
from django.contrib.gis.db import models as gis_models
from django.db.models import (
    ForeignKey, OneToOneField, BooleanField, CharField, TextField,
    IntegerField, DateTimeField, FloatField, SET_NULL, CASCADE
)
from worsica_web_intermediate import settings
from . import logger

worsica_logger = logger.init_logger('WORSICA-Intermediate.Models', settings.LOG_PATH)
worsica_logger.info('worsica_api.models')


class JobSubmission(models_base.Name):
    class State:

        SUBMITTED = 'submitted'
        DOWNLOADING = 'downloading'
        DOWNLOAD_WAITING_LTA = 'download-waiting-lta'
        DOWNLOAD_TIMEOUT_RETRY = 'download-timeout-retry'
        DOWNLOADED = 'downloaded'
        UPLOADING = 'uploading'
        UPLOADED = 'uploaded'
        CONVERTING = 'converting'
        CONVERTED = 'converted'
        RESAMPLING = 'resampling'
        RESAMPLED = 'resampled'
        MERGING = 'merging'
        MERGED = 'merged'
        GENERATING_VIRTUAL_PRODUCTS = 'generating-virtual'  # vp
        GENERATED_VIRTUAL_PRODUCTS = 'generated-virtual'  # vp
        INTERPOLATING_PRODUCTS = 'interpolating-products'  # interpolate
        INTERPOLATED_PRODUCTS = 'interpolated-products'  # wadi interpolate
        PROCESSING = 'processing'
        PROCESSED = 'processed'
        GENERATING_AVERAGE = 'generating-average'  # generate climatology
        GENERATED_AVERAGE = 'generated-average'  # generate climatology
        SUCCESS = 'success'

        ERROR_CONVERTING = 'error-converting'
        ERROR_DOWNLOADING = 'error-downloading'
        ERROR_DOWNLOAD_CORRUPT = 'error-download-corrupt'
        ERROR_UPLOADING = 'error-uploading'
        ERROR_RESAMPLING = 'error-resampling'
        ERROR_MERGING = 'error-merging'
        ERROR_GENERATE_VIRTUAL_PRODUCTS = 'error-generating-virtual'  # wadi vp
        ERROR_INTERPOLATE_PRODUCTS = 'error-interpolating-products'  # wadi interpolate
        ERROR_PROCESSING = 'error-processing'
        ERROR_GENERATING_AVERAGE = 'error-generating-average'  # wadi generate climatology
        # old
        STORING = 'storing'
        STORED = 'stored'
        ERROR_STORING = 'error-storing'

        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (DOWNLOADING, 'Downloading'),
            (DOWNLOAD_WAITING_LTA, 'Download waiting for LTA'),
            (DOWNLOAD_TIMEOUT_RETRY, 'Download timed out, retry'),
            (DOWNLOADED, 'Finished Download'),
            (UPLOADING, 'Uploading'),
            (UPLOADED, 'Finished Upload'),
            (CONVERTING, 'Converting'),
            (CONVERTED, 'Finished conversion'),
            (RESAMPLING, 'Resampling imagesets'),
            (RESAMPLED, 'Finished resampling imagesets'),
            (MERGING, 'Merging imagesets'),
            (MERGED, 'Finished merging imagesets'),
            (GENERATING_VIRTUAL_PRODUCTS, 'Generating virtual images'),  # wadi vp
            (GENERATED_VIRTUAL_PRODUCTS, 'Generated virtual images'),  # wadi vp
            (INTERPOLATING_PRODUCTS, 'Interpolating products'),  # wadi interpolate
            (INTERPOLATED_PRODUCTS, 'Interpolated products'),  # wadi interpolate
            (PROCESSING, 'Processing'),
            (PROCESSED, 'Finished Process'),
            (GENERATING_AVERAGE, 'Generating climatology'),  # wadi generate climatology
            (GENERATED_AVERAGE, 'Generated climatology'),  # wadi generate climatology
            (SUCCESS, 'Success'),
            (ERROR_CONVERTING, 'Error Converting'),
            (ERROR_DOWNLOADING, 'Error on Downloading'),
            (ERROR_DOWNLOAD_CORRUPT, 'Error download file corrupt'),
            (ERROR_UPLOADING, 'Error on Uploading'),
            (ERROR_RESAMPLING, 'Error on Resampling imagesets'),
            (ERROR_MERGING, 'Error on Merging imagesets'),
            (ERROR_GENERATE_VIRTUAL_PRODUCTS, 'Error on Generating virtual images'),  # wadi vp
            (ERROR_INTERPOLATE_PRODUCTS, 'Error on Interpolating products'),  # wadi interpolate
            (ERROR_PROCESSING, 'Error on Processing'),
            (ERROR_GENERATING_AVERAGE, 'Error on Generating climatology'),  # wadi generate climatology)
            # old
            (STORING, 'Storing'),
            (STORED, 'Stored'),
            (ERROR_STORING, 'Error on Storing'),
        )

    class Provider:
        SENTINEL1 = 'sentinel1'
        SENTINEL2 = 'sentinel2'
        PLEIADES = 'pleiades'
        DRONE = 'drone'
        TERRASARX = 'terrasar-x'

        _CHOICES = (
            (SENTINEL1, 'Sentinel 1 (Public)'),
            (SENTINEL2, 'Sentinel 2 (Public)'),
            (PLEIADES, 'Pleiades (Private)'),
            (DRONE, 'Drone (Private)'),
            (TERRASARX, 'TerraSAR-X'),
        )

    class Service:
        INLAND = 'inland'
        COASTAL = 'coastal'
        WATERLEAK = 'waterleak'

        _CHOICES = (
            (INLAND, 'Inland Detection'),
            (COASTAL, 'Coastline Detection'),
            (WATERLEAK, 'Water-Leak Detection'),
        )

    user_id = IntegerField('ID User', null=True, blank=True)
    aos_id = IntegerField('ID Region of Interest', null=True, blank=True)
    simulation_id = IntegerField('ID Simulation', null=True, blank=True)

    service = CharField(editable=True, choices=Service._CHOICES, default=Service.COASTAL, max_length=40)
    provider = CharField(editable=True, choices=Provider._CHOICES, default=Provider.SENTINEL2, max_length=40)
    state = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=40)

    exec_arguments = TextField(blank=True)
    obj = TextField(blank=True)

    lastSubmittedAt = DateTimeField(default=datetime.now, blank=False)
    lastRunAt = DateTimeField(null=True, blank=True)
    lastFinishedAt = DateTimeField(null=True, blank=True)

    isVisible = BooleanField(default=True)

    def __str__(self):
        return self.name  # 'workspace'+str(self.workspace.id)+'-simulation'+str(self.id)

# like jobsubmission, this will be a simple model to store a user chosen reference, which will be associated to a procimageset and its mergedproducts


class UserChosenProduct(models_base.Name):
    class State:

        SUBMITTED = 'submitted'
        DOWNLOADING = 'downloading'
        DOWNLOAD_WAITING_LTA = 'download-waiting-lta'
        DOWNLOAD_TIMEOUT_RETRY = 'download-timeout-retry'
        DOWNLOADED = 'downloaded'
        CONVERTING = 'converting'
        CONVERTED = 'converted'
        RESAMPLING = 'resampling'
        RESAMPLED = 'resampled'
        MERGING = 'merging'
        MERGED = 'merged'
        PROCESSING = 'processing'
        PROCESSED = 'processed'

        SUCCESS = 'success'
        ERROR_CONVERTING = 'error-converting'
        ERROR_DOWNLOADING = 'error-downloading'
        ERROR_DOWNLOAD_CORRUPT = 'error-download-corrupt'
        ERROR_RESAMPLING = 'error-resampling'
        ERROR_MERGING = 'error-merging'
        ERROR_PROCESSING = 'error-processing'
        # old
        STORING = 'storing'
        STORED = 'stored'
        ERROR_STORING = 'error-storing'

        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (DOWNLOADING, 'Downloading'),
            (DOWNLOAD_WAITING_LTA, 'Download waiting for LTA'),
            (DOWNLOAD_TIMEOUT_RETRY, 'Download timed out, retry'),
            (DOWNLOADED, 'Finished Download'),
            (CONVERTING, 'Converting'),
            (CONVERTED, 'Finished conversion'),
            (RESAMPLING, 'Resampling imagesets'),
            (RESAMPLED, 'Finished resampling imagesets'),
            (MERGING, 'Merging imagesets'),
            (MERGED, 'Finished merging imagesets'),
            (PROCESSING, 'Processing'),
            (PROCESSED, 'Finished Process'),
            (SUCCESS, 'Success'),
            (ERROR_CONVERTING, 'Error Converting'),
            (ERROR_DOWNLOADING, 'Error on Downloading'),
            (ERROR_DOWNLOAD_CORRUPT, 'Error download file corrupt'),
            (ERROR_RESAMPLING, 'Error on Resampling imagesets'),
            (ERROR_MERGING, 'Error on Merging imagesets'),
            (ERROR_PROCESSING, 'Error on Processing'),
            # old
            (STORING, 'Storing'),
            (STORED, 'Stored'),
            (ERROR_STORING, 'Error on Storing'),

        )

    class Provider:
        SENTINEL1 = 'sentinel1'
        SENTINEL2 = 'sentinel2'
        PLEIADES = 'pleiades'
        DRONE = 'drone'
        TERRASARX = 'terrasar-x'

        _CHOICES = (
            (SENTINEL1, 'Sentinel 1 (Public)'),
            (SENTINEL2, 'Sentinel 2 (Public)'),
            (PLEIADES, 'Pleiades (Private)'),
            (DRONE, 'Drone (Private)'),
            (TERRASARX, 'TerraSAR-X'),
        )

    class Service:
        INLAND = 'inland'
        COASTAL = 'coastal'
        WATERLEAK = 'waterleak'

        _CHOICES = (
            (INLAND, 'Inland Detection'),
            (COASTAL, 'Coastline Detection'),
            (WATERLEAK, 'Water-Leak Detection'),
        )

    user_id = IntegerField('ID User', null=True, blank=True)
    aos_id = IntegerField('ID Region of Interest', null=True, blank=True)

    service = CharField(editable=True, choices=Service._CHOICES, default=Service.COASTAL, max_length=40)
    provider = CharField(editable=True, choices=Provider._CHOICES, default=Provider.SENTINEL2, max_length=40)
    state = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=30)

    # managedByLeakDetectionId = ld[ID]
    # store leak detection identifier during its execution.
    managedByLeakDetectionId = CharField('Managed by leak detection id', null=True, blank=True, editable=False, max_length=60)

    lastSubmittedAt = DateTimeField(default=datetime.now, blank=False)
    lastRunAt = DateTimeField(null=True, blank=True)
    lastFinishedAt = DateTimeField(null=True, blank=True)
    imageDate = DateTimeField(null=True, blank=True)

    isVisible = BooleanField(default=True)

    def __str__(self):
        return self.name  # 'workspace'+str(self.workspace.id)+'-simulation'+str(self.id)


class RepositoryImageSets(models_base.ImageSet):
    # Sentinel
    class State:
        SUBMITTED = 'submitted'
        DOWNLOAD_WAITING_LTA = 'download-waiting-lta'
        DOWNLOAD_TIMEOUT_RETRY = 'download-timeout-retry'
        DOWNLOADING = 'downloading'
        DOWNLOADED = 'downloaded'
        CONVERTING = 'converting'
        CONVERTED = 'converted'
        ERROR_DOWNLOADING = 'error-downloading'
        ERROR_DOWNLOAD_CORRUPT = 'error-download-corrupt'
        ERROR_CONVERTING = 'error-converting'
        EXPIRED = 'expired'
        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (DOWNLOAD_WAITING_LTA, 'Download waiting for LTA'),
            (DOWNLOAD_TIMEOUT_RETRY, 'Download timed out, retry'),
            (DOWNLOADING, 'Downloading'),
            (DOWNLOADED, 'Finished Download'),
            (CONVERTING, 'Converting'),
            (CONVERTED, 'Finished conversion'),
            (ERROR_DOWNLOADING, 'Error on Downloading'),
            (ERROR_DOWNLOAD_CORRUPT, 'Error download file corrupt'),
            (ERROR_CONVERTING, 'Error Converting'),
            (EXPIRED, 'Expired'),
        )

    class Provider:
        SENTINEL1 = 'sentinel1'
        SENTINEL2 = 'sentinel2'

        _CHOICES = (
            (SENTINEL1, 'Sentinel 1 (downloaded)'),
            (SENTINEL2, 'Sentinel 2 (downloaded)'),
        )

    def now_plus_30():
        return datetime.now() + timedelta(days=30)

    state = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=40)
    provider = CharField(editable=True, choices=Provider._CHOICES, default=Provider.SENTINEL2, max_length=60)

    issueDate = DateTimeField(default=datetime.now, blank=True, editable=False)
    expirationDate = DateTimeField(default=now_plus_30, blank=True, editable=False)
    uploadedByUserId = IntegerField('Uploaded by user id', null=True, blank=True)

    childRepositoryImageSet = ForeignKey('self', null=True, on_delete=SET_NULL)
    # store the simulation/userchosen identifier for the downloaded imageset
    managedByJobId = CharField('Managed by Job/UserChosenProduct id', null=True, blank=True, editable=False, max_length=60)

    def __str__(self):              # __unicode__ on Python 2
        return self.name

#


class UserRepository(models_base.Name):
    portal_user_id = IntegerField('ID User', editable=False, null=True, blank=True)

#  Store user imagesets that will be available for any service


class UserRepositoryImageSets(models_base.ImageSet):
    class State:
        SUBMITTED = 'submitted'
        UPLOADING = 'uploading'
        UPLOADED = 'uploaded'
        ERROR_UPLOADING = 'error-uploading'
        EXPIRED = 'expired'
        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (UPLOADING, 'Uploading'),
            (UPLOADED, 'Finished upload'),
            (ERROR_UPLOADING, 'Error on uploading'),
            (EXPIRED, 'Expired'),
        )

    class Provider:
        PLEIADES = 'pleiades'
        DRONE = 'drone'
        TERRASARX = 'terrasar-x'

        _CHOICES = (
            (PLEIADES, 'Pleiades (uploaded)'),
            (DRONE, 'Drone (uploaded)'),
            (TERRASARX, 'TerraSAR-X (uploaded)'),
        )

    def now_plus_30():
        return datetime.now() + timedelta(days=30)

    state = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=40)
    provider = CharField(editable=True, choices=Provider._CHOICES, default=Provider.PLEIADES, max_length=60)
    issueDate = DateTimeField(default=datetime.now, blank=True, editable=False)
    uploadDate = DateTimeField(blank=False, null=True)
    wkt_bounds = CharField(max_length=300, blank=False, null=True)
    thumbnailImage = models.BinaryField(blank=True)
    image_arguments = TextField(blank=True)
    user_repository = ForeignKey(UserRepository, null=True, on_delete=SET_NULL)  # managed means either uploaded or downloaded by

    def __str__(self):              # __unicode__ on Python 2
        return self.name


class DataverseSubmission(models_base.Name):
    class State:

        SUBMITTED = 'submitted'
        DATAVERSE_UPLOADING = 'dataverse-uploading'
        DATAVERSE_UPLOADED = 'dataverse-uploaded'
        ERROR_DATAVERSE_UPLOADING = 'error-dataverse-uploading'

        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (DATAVERSE_UPLOADING, 'Dataverse Uploading'),
            (DATAVERSE_UPLOADED, 'Dataverse Uploaded'),
            (ERROR_DATAVERSE_UPLOADING, 'Error on Dataverse Uploading'),
        )

    jobSubmission = ForeignKey(JobSubmission, null=True, on_delete=CASCADE)
    doi = CharField(max_length=200)
    state = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=40)
    creationDate = DateTimeField(default=datetime.now, blank=True)  # , editable=False)


class ProcessImageSet(models_base.ImageSet):
    class State:

        SUBMITTED = 'submitted'
        DOWNLOAD_WAITING_LTA = 'download-waiting-lta'
        DOWNLOAD_TIMEOUT_RETRY = 'download-timeout-retry'
        DOWNLOADING = 'downloading'
        DOWNLOADED = 'downloaded'
        UPLOADING = 'uploading'
        UPLOADED = 'uploaded'
        CONVERTING = 'converting'
        CONVERTED = 'converted'
        RESAMPLING = 'resampling'
        RESAMPLED = 'resampled'
        SUCCESS = 'success'
        ERROR_CONVERTING = 'error-converting'
        ERROR_DOWNLOADING = 'error-downloading'
        ERROR_DOWNLOAD_CORRUPT = 'error-download-corrupt'
        ERROR_UPLOADING = 'error-uploading'
        ERROR_RESAMPLING = 'error-resampling'

        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (DOWNLOAD_WAITING_LTA, 'Download waiting for LTA'),
            (DOWNLOAD_TIMEOUT_RETRY, 'Download timed out, retry'),
            (DOWNLOADING, 'Downloading'),
            (DOWNLOADED, 'Finished Download'),
            (UPLOADING, 'Uploading'),
            (UPLOADED, 'Finished Upload'),
            (CONVERTING, 'Converting'),
            (CONVERTED, 'Finished conversion'),
            (RESAMPLING, 'Resampling'),
            (RESAMPLED, 'Resampled'),
            (SUCCESS, 'Success'),
            (ERROR_CONVERTING, 'Error Converting'),
            (ERROR_DOWNLOADING, 'Error on Downloading'),
            (ERROR_DOWNLOAD_CORRUPT, 'Error download file corrupt'),
            (ERROR_UPLOADING, 'Error on Uploading'),
            (ERROR_RESAMPLING, 'Error on Resampling'),
        )

    processState = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=40)
    small_name = CharField(max_length=200)

    jobSubmission = ForeignKey(JobSubmission, null=True, on_delete=CASCADE)
    imageSet = ForeignKey(RepositoryImageSets, on_delete=SET_NULL, blank=True, null=True)
    uploadedimageSet = ForeignKey(UserRepositoryImageSets, on_delete=SET_NULL, blank=True, null=True)
    convertToL2A = BooleanField(default=False)

    def __str__(self):
        return self.name


class ProcessImageSetRaster(models_base.IndexImageSet):
    # indexType is in IndexImageSet

    processImageSet = ForeignKey(ProcessImageSet, null=True, on_delete=CASCADE)
    rasterLayer = OneToOneField(raster_models.RasterLayer, on_delete=CASCADE, blank=True, null=True)
    autoThresholdValue = FloatField('Auto threshold value', null=True, blank=True)

    def __str__(self):
        return self.name + ' (' + self.indexType + ')'

# generate topography


class GenerateTopographyMap(models_base.Name):
    class State:

        SUBMITTED = 'submitted'
        GENERATING = 'generating-topo'
        GENERATED = 'generated-topo'
        STORING_GENERATING = 'storing-generated-topo'
        STORED_GENERATING = 'stored-generated-topo'
        SUCCESS = 'success'
        ERROR_GENERATING = 'error-generating-topo'
        ERROR_STORING_GENERATING = 'error-storing-generated-topo'

        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (GENERATING, 'Generating'),
            (GENERATED, 'Generated'),
            (STORING_GENERATING, 'Storing topography'),
            (STORED_GENERATING, 'Stored topography'),
            (SUCCESS, 'Success'),
            (ERROR_GENERATING, 'Error on Generating'),
            (ERROR_STORING_GENERATING, 'Error on storing topography'),
        )

    class GenerateFrom:
        PROBING = 'probing'
        UPLOAD = 'upload'

        _CHOICES = (
            (PROBING, 'From probing'),
            (UPLOAD, 'From file upload')
        )

    processState = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=40)
    generateTopographyFrom = CharField(editable=True, choices=GenerateFrom._CHOICES, default=GenerateFrom.PROBING, max_length=40)
    small_name = CharField(max_length=200)
    jobSubmission = ForeignKey(JobSubmission, null=True, on_delete=CASCADE)

    def __str__(self):
        return self.name


class GenerateTopographyMapRaster(models_base.IndexImageSet):

    # indexType is in IndexImageSet
    class Kind:
        TOPOGRAPHY = 'topo'
        FLOODFREQ = 'flood-freq'

        _CHOICES = (
            (TOPOGRAPHY, 'Topography'),
            (FLOODFREQ, 'Flood Frequency')
        )

    generateTopographyMap = ForeignKey(GenerateTopographyMap, null=True, on_delete=CASCADE)
    topoKind = CharField(editable=True, choices=Kind._CHOICES, default=Kind.TOPOGRAPHY, max_length=40)
    rasterLayer = OneToOneField(raster_models.RasterLayer, on_delete=CASCADE, blank=True, null=True)

    def __str__(self):
        return self.name + ' (' + self.indexType + ')'


class MergedProcessImageSet(models_base.Name):
    class State:

        SUBMITTED = 'submitted'
        MERGING = 'merging'
        MERGED = 'merged'
        GENERATING_VIRTUAL_PRODUCTS = 'generating-virtual'  # wadi vp
        GENERATED_VIRTUAL_PRODUCTS = 'generated-virtual'  # wadi vp
        INTERPOLATING_PRODUCTS = 'interpolating-products'  # wadi interpolate
        INTERPOLATED_PRODUCTS = 'interpolated-products'  # wadi interpolate
        PROCESSING = 'processing'
        PROCESSED = 'processed'
        STORING_PROCESSING = 'storing-processing'
        STORED_PROCESSING = 'stored-processing'
        SUCCESS = 'success'
        ERROR_MERGING = 'error-merging'
        ERROR_GENERATE_VIRTUAL_PRODUCTS = 'error-generating-virtual'  # wadi vp
        ERROR_INTERPOLATE_PRODUCTS = 'error-interpolating-products'  # wadi interpolate
        ERROR_PROCESSING = 'error-processing'
        ERROR_STORING_PROCESSING = 'error-storing-processing'
        # old
        STORING = 'storing'
        ERROR_STORING = 'error-storing'

        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (MERGING, 'Merging'),
            (MERGED, 'Merged'),
            (GENERATING_VIRTUAL_PRODUCTS, 'Generating virtual images'),  # wadi vp
            (GENERATED_VIRTUAL_PRODUCTS, 'Generated virtual images'),  # wadi vp
            (INTERPOLATING_PRODUCTS, 'Interpolating products'),  # wadi interpolate
            (INTERPOLATED_PRODUCTS, 'Interpolated products'),  # wadi interpolate
            (PROCESSING, 'Processing'),
            (PROCESSED, 'Finished Process'),
            (STORING_PROCESSING, 'Storing processing'),
            (STORED_PROCESSING, 'Stored processing'),
            (SUCCESS, 'Success'),
            (ERROR_MERGING, 'Error on Merging'),
            (ERROR_GENERATE_VIRTUAL_PRODUCTS, 'Error on Generating virtual images'),  # wadi vp
            (ERROR_INTERPOLATE_PRODUCTS, 'Error on Interpolating products'),  # wadi interpolate
            (ERROR_PROCESSING, 'Error on Processing'),
            (ERROR_STORING_PROCESSING, 'Error on Storing processing'),
            (STORING, 'Storing'),
            (ERROR_STORING, 'Error on Storing'),
        )

    processState = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=40)
    small_name = CharField(max_length=200)
    isVirtual = BooleanField('Virtual image?', default=False)
    sensingDate = DateTimeField(null=True, blank=True)
    jobSubmission = ForeignKey(JobSubmission, null=True, on_delete=CASCADE)
    procImageSet_ids = CharField(default='', editable=False, null=True, blank=True, max_length=200)
    interpolationImageSet_ids = CharField('IDs of images for interpolation', default='', editable=False, null=True, blank=True, max_length=200)

    def __str__(self):
        return self.name + ' - ' + self.jobSubmission.name


class MergedProcessImageSetRaster(models_base.IndexImageSet):
    # indexType is in IndexImageSet

    processImageSet = ForeignKey(MergedProcessImageSet, null=True, on_delete=CASCADE)
    rasterLayer = OneToOneField(raster_models.RasterLayer, on_delete=CASCADE, blank=True, null=True)
    autoThresholdValue = FloatField('Auto threshold value', null=True, blank=True)
    clone_cleanup_small_name = CharField(default='', editable=False, null=True, blank=True, max_length=200)
    isFromManualThreshold = BooleanField(default=False)

    def __str__(self):
        return self.name + ' (' + self.indexType + ') ' + self.clone_cleanup_small_name


class ImageSetThreshold(models_base.Name):
    class State:
        SUBMITTED = 'submitted'
        CALCULATING_THR = 'calculating-threshold'
        ERROR_CALCULATING_THR = 'error-calculating-threshold'
        FINISHED = 'finished'  # in case of normal image dump from cloning
        NOT_APPLICABLE = 'not-applicable'  # in case of normal image dump from normal processing

        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (CALCULATING_THR, 'Calculating threshold'),
            (ERROR_CALCULATING_THR, 'Error calculating threshold'),
            (FINISHED, 'Finished'),
            (NOT_APPLICABLE, 'Not applicable'),
        )

    processImageSet = ForeignKey(MergedProcessImageSet, null=True, on_delete=CASCADE)
    small_name = CharField(max_length=200)
    threshold = FloatField('Threshold value', default=0, null=True, blank=True)
    state = CharField('Threshold state', editable=True, choices=State._CHOICES, default=State.NOT_APPLICABLE, max_length=40)  # by default, all created shapelinempis are NOT_APPLICABLE

    def __str__(self):
        return self.name


class ImageSetThresholdRaster(models_base.IndexImageSet):
    imageSetThreshold = ForeignKey(ImageSetThreshold, null=True, on_delete=CASCADE)
    rasterLayer = OneToOneField(raster_models.RasterLayer, on_delete=CASCADE, blank=True, null=True)
    thresholdValue = FloatField('Threshold value', null=True, blank=True)

    def __str__(self):
        return self.name + ' (' + self.indexType + ')'


# download user chosen images for processing for leak detection?
class UserChosenProcessImageSet(models_base.ImageSet):
    class State:

        SUBMITTED = 'submitted'
        DOWNLOAD_WAITING_LTA = 'download-waiting-lta'
        DOWNLOAD_TIMEOUT_RETRY = 'download-timeout-retry'
        DOWNLOADING = 'downloading'
        DOWNLOADED = 'downloaded'
        UPLOADING = 'uploading'
        UPLOADED = 'uploaded'
        CONVERTING = 'converting'
        CONVERTED = 'converted'
        RESAMPLING = 'resampling'
        RESAMPLED = 'resampled'
        SUCCESS = 'success'
        ERROR_CONVERTING = 'error-converting'
        ERROR_DOWNLOADING = 'error-downloading'
        ERROR_DOWNLOAD_CORRUPT = 'error-download-corrupt'
        ERROR_UPLOADING = 'error-uploading'
        ERROR_RESAMPLING = 'error-resampling'

        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (DOWNLOAD_WAITING_LTA, 'Download waiting for LTA'),
            (DOWNLOAD_TIMEOUT_RETRY, 'Download timed out, retry'),
            (DOWNLOADING, 'Downloading'),
            (DOWNLOADED, 'Finished Download'),
            (UPLOADING, 'Uploading'),
            (UPLOADED, 'Finished Upload'),
            (CONVERTING, 'Converting'),
            (CONVERTED, 'Finished conversion'),
            (RESAMPLING, 'Resampling'),
            (RESAMPLED, 'Resampled'),
            (SUCCESS, 'Success'),
            (ERROR_CONVERTING, 'Error Converting'),
            (ERROR_DOWNLOADING, 'Error on Downloading'),
            (ERROR_DOWNLOAD_CORRUPT, 'Error download file corrupt'),
            (ERROR_UPLOADING, 'Error on Uploading'),
            (ERROR_RESAMPLING, 'Error on Resampling'),
        )

    processState = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=40)
    small_name = CharField(max_length=200)

    user_chosen_product = ForeignKey(UserChosenProduct, null=True, on_delete=CASCADE)
    imageSet = ForeignKey(RepositoryImageSets, on_delete=SET_NULL, blank=True, null=True)
    convertToL2A = BooleanField(default=False)

    def __str__(self):
        return self.name


class UserChosenMergedProcessImageSet(models_base.Name):
    class State:

        SUBMITTED = 'submitted'
        MERGING = 'merging'
        MERGED = 'merged'
        GENERATING_VIRTUAL_PRODUCTS = 'generating-virtual'  # wadi vp
        GENERATED_VIRTUAL_PRODUCTS = 'generated-virtual'  # wadi vp
        INTERPOLATING_PRODUCTS = 'interpolating-products'  # wadi interpolate
        INTERPOLATED_PRODUCTS = 'interpolated-products'  # wadi interpolate
        PROCESSING = 'processing'
        PROCESSED = 'processed'
        STORING_PROCESSING = 'storing-processing'
        STORED_PROCESSING = 'stored-processing'
        SUCCESS = 'success'
        ERROR_MERGING = 'error-merging'
        ERROR_GENERATE_VIRTUAL_PRODUCTS = 'error-generating-virtual'  # wadi vp
        ERROR_INTERPOLATE_PRODUCTS = 'error-interpolating-products'  # wadi interpolate
        ERROR_PROCESSING = 'error-processing'
        ERROR_STORING_PROCESSING = 'error-storing-processing'
        # old
        STORING = 'storing'
        ERROR_STORING = 'error-storing'

        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (MERGING, 'Merging'),
            (MERGED, 'Merged'),
            (GENERATING_VIRTUAL_PRODUCTS, 'Generating virtual images'),  # wadi vp
            (GENERATED_VIRTUAL_PRODUCTS, 'Generated virtual images'),  # wadi vp
            (INTERPOLATING_PRODUCTS, 'Interpolating products'),  # wadi interpolate
            (INTERPOLATED_PRODUCTS, 'Interpolated products'),  # wadi interpolate
            (PROCESSING, 'Processing'),
            (PROCESSED, 'Finished Process'),
            (STORING_PROCESSING, 'Storing processing'),
            (STORED_PROCESSING, 'Stored processing'),
            (SUCCESS, 'Success'),
            (ERROR_MERGING, 'Error on Merging'),
            (ERROR_GENERATE_VIRTUAL_PRODUCTS, 'Error on Generating virtual images'),  # wadi vp
            (ERROR_INTERPOLATE_PRODUCTS, 'Error on Interpolating products'),  # wadi interpolate
            (ERROR_PROCESSING, 'Error on Processing'),
            (ERROR_STORING_PROCESSING, 'Error on Storing processing'),
            # old
            (STORING, 'Storing'),
            (ERROR_STORING, 'Error on Storing'),
        )

    processState = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=40)
    small_name = CharField(max_length=200)
    sensingDate = DateTimeField(null=True, blank=True)
    user_chosen_product = ForeignKey(UserChosenProduct, null=True, on_delete=CASCADE)
    procImageSet_ids = CharField(default='', editable=False, null=True, blank=True, max_length=200)  # user chosen proc ids

    def __str__(self):
        return self.name + ' - ' + self.user_chosen_product.name


class UserChosenMergedProcessImageSetRaster(models_base.IndexImageSet):
    # indexType is in IndexImageSet

    processImageSet = ForeignKey(UserChosenMergedProcessImageSet, null=True, on_delete=CASCADE)
    rasterLayer = OneToOneField(raster_models.RasterLayer, on_delete=CASCADE, blank=True, null=True)
    autoThresholdValue = FloatField('Auto threshold value', null=True, blank=True)

    def __str__(self):
        return self.name + ' (' + self.indexType + ')'


# job submission for leak detection
class MergedProcessImageSetForLeakDetection(models_base.IndexImageSet):
    # indexType is in IndexImageSet
    class State:
        SUBMITTED = 'submitted'
        # if users wants to download another image
        DOWNLOADING = 'downloading'
        DOWNLOAD_WAITING_LTA = 'download-waiting-lta'
        DOWNLOAD_TIMEOUT_RETRY = 'download-timeout-retry'
        DOWNLOADED = 'downloaded'
        ERROR_DOWNLOADING = 'error-downloading'
        ERROR_DOWNLOAD_CORRUPT = 'error-download-corrupt'
        CONVERTING = 'converting'
        CONVERTED = 'converted'
        ERROR_CONVERTING = 'error-converting'
        RESAMPLING = 'resampling'
        RESAMPLED = 'resampled'
        ERROR_RESAMPLING = 'error-resampling'
        STORING_RGB = 'storing-rgb'  # coastal/inland after merge
        STORED_RGB = 'stored-rgb'  # coastal/inland after merge
        ERROR_STORING_RGB = 'error-storing-rgb'  # coastal/inland after merge
        MERGING = 'merging'
        MERGED = 'merged'
        ERROR_MERGING = 'error-merging'
        PROCESSING = 'processing'
        PROCESSED = 'processed'
        ERROR_PROCESSING = 'error-processing'
        STORING_PROCESSING = 'storing-processing'
        STORED_PROCESSING = 'stored-processing'
        ERROR_STORING_PROCESSING = 'error-storing-processing'
        # old
        STORING = 'storing'
        STORED = 'stored'
        ERROR_STORING = 'error-storing'
        # anomaly
        CALCULATING_DIFF = 'calculating-diff'
        CALCULATED_DIFF = 'calculated-diff'
        ERROR_CALCULATING_DIFF = 'error-calculating-diff'
        STORING_CALCULATED_DIFF = 'storing-calculated-diff'
        STORED_CALCULATED_DIFF = 'stored-calculated-diff'
        ERROR_STORING_CALCULATED_DIFF = 'error-storing-calculated-diff'
        # 2nd deriv
        CALCULATING_LEAK = 'calculating-leak'
        CALCULATED_LEAK = 'calculated-leak'
        ERROR_CALCULATING_LEAK = 'error-calculating-leak'
        STORING_CALCULATED_LEAK = 'storing-calculated-leak'
        STORED_CALCULATED_LEAK = 'stored-calculated-leak'
        ERROR_STORING_CALCULATED_LEAK = 'error-storing-calculated-leak'
        # leak filtering
        GENERATING_MASK_LEAK = 'generating-mask-leak'
        GENERATED_MASK_LEAK = 'generated-mask-leak'
        ERROR_GENERATING_MASK_LEAK = 'error-generating-mask-leak'
        # identify leak
        DETERMINATING_LEAK = 'determinating-leak'
        DETERMINATED_LEAK = 'determinated-leak'
        ERROR_DETERMINATING_LEAK = 'error-determinating-leak'
        STORING_DETERMINATED_LEAK = 'storing-determinated-leak'
        STORED_DETERMINATED_LEAK = 'stored-determinated-leak'
        ERROR_STORING_DETERMINATED_LEAK = 'error-storing-determinated-leak'
        # complete
        SUCCESS = 'success'

        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (DOWNLOADING, 'Downloading'),
            (DOWNLOAD_WAITING_LTA, 'Download waiting for LTA'),
            (DOWNLOAD_TIMEOUT_RETRY, 'Download timed out, retry'),
            (DOWNLOADED, 'Finished Download'),
            (ERROR_DOWNLOADING, 'Error on Downloading'),
            (ERROR_DOWNLOAD_CORRUPT, 'Error download file corrupt'),
            (CONVERTING, 'Converting'),
            (CONVERTED, 'Finished conversion'),
            (ERROR_CONVERTING, 'Error Converting'),
            (RESAMPLING, 'Resampling imagesets'),
            (RESAMPLED, 'Finished resampling imagesets'),
            (ERROR_RESAMPLING, 'Error on Resampling imagesets'),
            (STORING_RGB, 'Storing RGB'),  # coastal/inland after merge
            (STORED_RGB, 'Stored RGB'),  # coastal/inland after merge
            (ERROR_STORING_RGB, 'Error on Storing RGB'),  # coastal/inland after merge
            (MERGING, 'Merging imagesets'),
            (MERGED, 'Finished merging imagesets'),
            (ERROR_MERGING, 'Error on Merging imagesets'),
            (PROCESSING, 'Processing'),
            (PROCESSED, 'Finished Process'),
            (ERROR_PROCESSING, 'Error on Processing'),
            (STORING_PROCESSING, 'Storing processing'),
            (STORED_PROCESSING, 'Stored processing'),
            (ERROR_STORING_PROCESSING, 'Error on Storing processing'),
            # old
            (STORING, 'Storing'),
            (STORED, 'Stored'),
            (ERROR_STORING, 'Error on Storing'),
            # anomaly
            (CALCULATING_DIFF, 'Calculating anomaly'),  # by_anomaly
            (CALCULATED_DIFF, 'Calculated anomaly'),
            (ERROR_CALCULATING_DIFF, 'Error on calculating anomaly'),
            (STORING_CALCULATED_DIFF, 'Storing calculated anomaly'),  # by_anomaly
            (STORED_CALCULATED_DIFF, 'Stored calculated anomaly'),
            (ERROR_STORING_CALCULATED_DIFF, 'Error storing calculated anomaly'),
            # 2nd deriv
            (CALCULATING_LEAK, 'Calculating 2nd deriv'),
            (CALCULATED_LEAK, 'Calculated 2nd deriv'),
            (ERROR_CALCULATING_LEAK, 'Error on calculating 2nd deriv'),
            (STORING_CALCULATED_LEAK, 'Storing calculated 2nd deriv'),
            (STORED_CALCULATED_LEAK, 'Stored calculated 2nd deriv'),
            (ERROR_STORING_CALCULATED_LEAK, 'Error storing calculated 2nd deriv'),
            # leak filtering
            (GENERATING_MASK_LEAK, 'Generating mask leaks'),
            (GENERATED_MASK_LEAK, 'Generated mask leaks'),
            (ERROR_GENERATING_MASK_LEAK, 'Error on generating mask leaks'),
            # identify leak
            (DETERMINATING_LEAK, 'Determinating possible leaks'),
            (DETERMINATED_LEAK, 'Determinated possible leaks'),
            (ERROR_DETERMINATING_LEAK, 'Error on identifying possible leaks'),
            (STORING_DETERMINATED_LEAK, 'Storing determinated possible leaks'),
            (STORED_DETERMINATED_LEAK, 'Stored determinated possible leaks'),
            (ERROR_STORING_DETERMINATED_LEAK, 'Error storing determinated possible leaks'),
            # complete
            (SUCCESS, 'Success'),
        )

    class DetermineLeaksMethod:
        ANOMALY = 'by_anomaly'
        INDEX = 'by_index'

        _CHOICES = (
            (ANOMALY, 'Anomaly'),
            (INDEX, 'Index')
        )

    class ImageSourceFrom:
        PROCESSED_IMAGE = 'processed_image'
        NEW_IMAGE = 'new_image'

        _CHOICES = (
            (PROCESSED_IMAGE, 'A Processed image'),
            (NEW_IMAGE, 'A new image')
        )
    jobSubmission = ForeignKey(JobSubmission, null=True, on_delete=CASCADE)
    exec_arguments = TextField(blank=True)

    ucprocessImageSet = ForeignKey(UserChosenMergedProcessImageSet, null=True, blank=True, on_delete=CASCADE)  # new image
    processImageSet = ForeignKey(MergedProcessImageSet, null=True, blank=True, on_delete=CASCADE)  # existing image

    processState = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=50)
    small_name = CharField(max_length=200)
    start_processing_second_deriv_from = CharField(choices=DetermineLeaksMethod._CHOICES, default=DetermineLeaksMethod.INDEX,
                                                   max_length=50)  # where to start calculating 2nd derivative and proceeding leak detection
    image_source_from = CharField(choices=ImageSourceFrom._CHOICES, default=ImageSourceFrom.PROCESSED_IMAGE, max_length=50)

    filter_by_mask = BooleanField(default=False)

    def __str__(self):
        return self.name


class MergedProcessImageSetForLeakDetectionRaster(models_base.Name):
    class LeakDetectionOutputType:
        ANOMALY = 'anomaly'
        ANOMALY_SECOND_DERIV = 'anomaly_2nd_deriv'
        SECOND_DERIV = '2nd_deriv'

        _CHOICES = (
            (ANOMALY, 'Anomaly'),
            (ANOMALY_SECOND_DERIV, 'Anomaly second derivative'),
            (SECOND_DERIV, 'Second derivative'),
        )

    ldType = CharField(choices=LeakDetectionOutputType._CHOICES, default=LeakDetectionOutputType.ANOMALY, max_length=20)
    processImageSet = ForeignKey(MergedProcessImageSetForLeakDetection, null=True, on_delete=CASCADE)
    rasterLayer = OneToOneField(raster_models.RasterLayer, on_delete=CASCADE, blank=True, null=True)

    def __str__(self):
        return self.name + ' (' + self.ldType + ')'


class MergedProcessImageSetForLeakDetectionMask(models_base.Name):
    class State:
        SUBMITTED = 'submitted'
        STORING = 'storing'
        STORED = 'stored'
        ERROR_STORING = 'error-storing'
        GENERATING_MASK = 'generating-mask'
        GENERATED_MASK = 'generated-mask'
        ERROR_GENERATING_MASK = 'error-generating-mask'
        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (STORING, 'Storing'),
            (STORED, 'Stored'),
            (ERROR_STORING, 'Error storing'),
            (GENERATING_MASK, 'Generating mask'),
            (GENERATED_MASK, 'Generated mask'),
            (ERROR_GENERATING_MASK, 'Error generating mask'),
        )

    state = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=40)
    processImageSet = ForeignKey(MergedProcessImageSetForLeakDetection, null=True, on_delete=CASCADE)

    wkt_bounds = CharField(max_length=300, blank=False, null=True)
    small_name = CharField(max_length=100, default="Unnamed")  # workspaceAA-aosBB-leakCC???

    generated_from_ids = CharField(max_length=300, blank=False, null=True, editable=False)

    def __str__(self):              # __unicode__ on Python 2
        return self.name


class MergedProcessImageSetForLeakDetectionMaskRaster(models_base.Name):
    ld_mask = ForeignKey(MergedProcessImageSetForLeakDetectionMask, null=True, on_delete=CASCADE)
    maskRasterLayer = OneToOneField(raster_models.RasterLayer, on_delete=CASCADE, blank=True, null=True)

    def __str__(self):              # __unicode__ on Python 2
        return self.name


class MergedProcessImageSetForLeakDetectionRasterLeakPoints(models_base.Name):
    second_deriv_raster = ForeignKey(MergedProcessImageSetForLeakDetectionRaster, on_delete=CASCADE, blank=True, null=True)
    small_name = CharField(max_length=100, default="Unnamed")  # workspaceAA-aosBB-leakCC???
    xCoordinate = FloatField()
    yCoordinate = FloatField()
    pixelValue = FloatField()
    showLeakPoint = BooleanField(default=True)

    def __str__(self):              # __unicode__ on Python 2
        return self.small_name

# TODO: STORE SAVED LEAK POINTS


class MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSaved(models_base.Name):
    leak_detection = ForeignKey(MergedProcessImageSetForLeakDetection, null=True, on_delete=CASCADE)
    small_name = CharField(max_length=100, default="Unnamed")  # workspaceAA-aosBB-leakCC???
    number_points = IntegerField('Number of points', null=True, blank=True)

    def __str__(self):              # __unicode__ on Python 2
        return self.small_name

# TODO: STORE SAVED LEAK POINTS


class MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPoints(models_base.Name):
    user_leak_points = ForeignKey(MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSaved, null=True, on_delete=CASCADE)
    small_name = CharField(max_length=100, default="Unnamed")  # workspaceAA-aosBB-leakCC???
    xCoordinate = FloatField()
    yCoordinate = FloatField()
    pixelValue = FloatField()
    showLeakPoint = BooleanField(default=True)

    def __str__(self):              # __unicode__ on Python 2
        return self.small_name


class AverageMergedProcessImageSet(models_base.Name):
    class State:
        SUBMITTED = 'submitted'
        GENERATING_AVERAGE = 'generating-average'  # wadi generate climatology
        GENERATED_AVERAGE = 'generated-average'  # wadi generate climatology
        ERROR_GENERATING_AVERAGE = 'error-generating-average'
        STORING_GENERATING_AVERAGE = 'storing-generating-average'
        ERROR_STORING_GENERATING_AVERAGE = 'error-storing-generating-average'
        SUCCESS = 'success'
        # old
        STORING = 'storing'
        ERROR_STORING = 'error-storing'

        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (GENERATING_AVERAGE, 'Generating climatology'),  # wadi generate climatology
            (GENERATED_AVERAGE, 'Generated climatology'),  # wadi generate climatology
            (ERROR_GENERATING_AVERAGE, 'Error on Generating climatology'),  # wadi generate climatology)
            (STORING_GENERATING_AVERAGE, 'Storing climatology'),
            (ERROR_STORING_GENERATING_AVERAGE, 'Error on Storing climatology'),
            (SUCCESS, 'Success'),
            # old
            (STORING, 'Storing'),
            (ERROR_STORING, 'Error on Storing'),
        )

    processState = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=50)
    jobSubmission = ForeignKey(JobSubmission, null=True, on_delete=CASCADE)
    date = CharField(max_length=20)  # mm-dd
    small_name = CharField(blank=False, max_length=200)
    mergedprocImageSet_ids = CharField('IDs of merged proc image', default='', editable=False, null=True, blank=True, max_length=200)

    def __str__(self):
        return self.name


class AverageMergedProcessImageSetRaster(models_base.IndexImageSet):
    # indexType is in IndexImageSet

    amprocessImageSet = ForeignKey(AverageMergedProcessImageSet, null=True, on_delete=CASCADE)
    rasterLayer = OneToOneField(raster_models.RasterLayer, on_delete=CASCADE, blank=True, null=True)

    def __str__(self):
        return self.name + ' (' + self.indexType + ')'


class ShapelineMPIS(models_base.Name):
    class ShapelineSourceType:
        FROM_PROCESSING = 'processing'
        FROM_CLONING_AND_CLEANING = 'cloning_and_cleaning'

        _CHOICES = (
            (FROM_PROCESSING, 'Processing'),
            (FROM_CLONING_AND_CLEANING, 'Cloning and cleaning'),
        )

    class State:
        SUBMITTED = 'submitted'
        CLONING_SHP = 'cloning'
        ERROR_CLONING_SHP = 'error-cloning'
        FINISHED = 'finished'  # in case of normal shapeline dump from cloning
        NOT_APPLICABLE = 'not-applicable'  # in case of normal shapeline dump from normal processing

        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (CLONING_SHP, 'Cloning'),
            (ERROR_CLONING_SHP, 'Error cloning'),
            (FINISHED, 'Finished'),
            (NOT_APPLICABLE, 'Not applicable'),
        )

    small_name = CharField(blank=False, max_length=200, default='(no name)')
    polygons_geojson = TextField(blank=True)
    processImageSet = ForeignKey(ProcessImageSet, blank=True, null=True, on_delete=CASCADE)
    mprocessImageSet = ForeignKey(MergedProcessImageSet, blank=True, null=True, on_delete=CASCADE)
    imageSetThreshold = ForeignKey(ImageSetThreshold, blank=True, null=True, on_delete=CASCADE)
    shapelineSourceType = CharField(choices=ShapelineSourceType._CHOICES, default=ShapelineSourceType.FROM_PROCESSING, max_length=40)
    state = CharField('Cloning state', editable=True, choices=State._CHOICES, default=State.NOT_APPLICABLE, max_length=40)  # by default, all created shapelinempis are NOT_APPLICABLE
    isFromManualThreshold = BooleanField(default=False)

    def get_jobsub(self):
        s = (self.processImageSet.jobSubmission.name if (self.processImageSet) else self.mprocessImageSet.jobSubmission.name if (self.mprocessImageSet) else '(Unknown)')
        return s

    def get_imageset(self):
        s = ('ProcessImageSet: ' + self.processImageSet.name if (self.processImageSet) else 'MergedProcessImageSet: ' + self.mprocessImageSet.name if (self.mprocessImageSet) else '(Unknown)')
        return s

    def get_source_type(self):
        s = 'Custom: ' + self.small_name if self.shapelineSourceType == 'cloning_and_cleaning' else 'Processing'
        return s


class Shapeline(gis_models.Model):
    processImageSet = ForeignKey(ProcessImageSet, null=True, on_delete=CASCADE)  # deprecated
    mprocessImageSet = ForeignKey(MergedProcessImageSet, null=True, on_delete=CASCADE)  # deprecated

    shapelineMPIS = ForeignKey(ShapelineMPIS, null=True, on_delete=CASCADE)
    name = CharField(max_length=100, default="Unnamed")

    dn = models.IntegerField('DN')
    dn_1 = models.IntegerField('DN_1')

    mls = gis_models.MultiLineStringField()

    def __str__(self):              # __unicode__ on Python 2
        return self.name


class UserRepositoryGeometry(models_base.Name):  # pipe networks, other lines, whatever
    class State:
        SUBMITTED = 'submitted'
        UPLOADING = 'uploading'
        UPLOADED = 'uploaded'
        ERROR_UPLOADING = 'error-uploading'
        STORING = 'storing'
        STORED = 'stored'
        ERROR_STORING = 'error-storing'
        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (UPLOADING, 'Uploading'),
            (UPLOADED, 'Finished upload'),
            (ERROR_UPLOADING, 'Error on uploading'),
            (STORING, 'Storing'),
            (STORED, 'Stored'),
            (ERROR_STORING, 'Error storing'),
        )

    class Service:
        INLAND = 'inland'
        COASTAL = 'coastal'
        WATERLEAK = 'waterleak'

        _CHOICES = (
            (INLAND, 'Inland Detection'),
            (COASTAL, 'Coastline Detection'),
            (WATERLEAK, 'Water-Leak Detection'),
        )

    state = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=40)
    user_repository = ForeignKey(UserRepository, null=True, on_delete=CASCADE)
    small_name = CharField(max_length=100, default="Unnamed")  # workspaceAA-aosBB-leakCC???
    wkt_bounds = CharField(max_length=300, blank=False, null=True)
    uploadDate = DateTimeField(blank=False, null=True)
    service = CharField(editable=True, choices=Service._CHOICES, default=Service.WATERLEAK, max_length=40)

    def __str__(self):              # __unicode__ on Python 2
        return self.name


class UserRepositoryGeometryShapeline(models_base.Name):
    user_repository_geom = ForeignKey(UserRepositoryGeometry, null=True, on_delete=CASCADE)
    name = CharField(max_length=512, default="Unnamed")
    mls = gis_models.MultiLineStringField()

    def __str__(self):              # __unicode__ on Python 2
        return self.name


class UserRepositoryLeakDetectionLeak(models_base.Name):
    class State:
        SUBMITTED = 'submitted'
        UPLOADING = 'uploading'
        UPLOADED = 'uploaded'
        ERROR_UPLOADING = 'error-uploading'
        STORING = 'storing'
        STORED = 'stored'
        ERROR_STORING = 'error-storing'
        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (UPLOADING, 'Uploading'),
            (UPLOADED, 'Finished upload'),
            (ERROR_UPLOADING, 'Error on uploading'),
            (STORING, 'Storing'),
            (STORED, 'Stored'),
            (ERROR_STORING, 'Error storing'),
        )
    state = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=40)
    user_repository = ForeignKey(UserRepository, null=True, on_delete=SET_NULL)
    small_name = CharField(max_length=100, default="Unnamed")  # workspaceAA-aosBB-leakCC???
    uploadDate = DateTimeField(blank=False, null=True)
    wkt_bounds = CharField(max_length=300, blank=False, null=True)

    def __str__(self):              # __unicode__ on Python 2
        return self.name


class UserRepositoryLeakDetectionLeakPoints(models_base.Name):
    user_repository_leak = ForeignKey(UserRepositoryLeakDetectionLeak, on_delete=CASCADE, blank=True, null=True)
    small_name = CharField(max_length=100, default="Unnamed")  # workspaceAA-aosBB-leakCC???
    xCoordinate = FloatField()
    yCoordinate = FloatField()
    pixelValue = FloatField()
    showLeakPoint = BooleanField(default=True)

    def __str__(self):              # __unicode__ on Python 2
        return self.small_name


class UserRepositoryLeakDetectionMask(models_base.Name):
    class State:
        SUBMITTED = 'submitted'
        UPLOADING = 'uploading'
        UPLOADED = 'uploaded'
        ERROR_UPLOADING = 'error-uploading'
        STORING = 'storing'
        STORED = 'stored'
        ERROR_STORING = 'error-storing'
        GENERATING_MASK = 'generating-mask'
        GENERATED_MASK = 'generated-mask'
        ERROR_GENERATING_MASK = 'error-generating-mask'
        _CHOICES = (
            (SUBMITTED, 'Submitted'),
            (UPLOADING, 'Uploading'),
            (UPLOADED, 'Finished upload'),
            (ERROR_UPLOADING, 'Error on uploading'),
            (STORING, 'Storing'),
            (STORED, 'Stored'),
            (ERROR_STORING, 'Error storing'),
            (GENERATING_MASK, 'Generating mask'),
            (GENERATED_MASK, 'Generated mask'),
            (ERROR_GENERATING_MASK, 'Error generating mask'),
        )

    class TypeMasks:
        UPLOADED_BY_USER = 'uploaded_by_user'
        GENERATED_FROM_SHAPEFILES = 'generated_from_shps'

        _CHOICES = (
            (UPLOADED_BY_USER, 'Mask uploaded by the user'),
            (GENERATED_FROM_SHAPEFILES, 'Mask generated from uploaded shapefiles'),
        )

    state = CharField(editable=True, choices=State._CHOICES, default=State.SUBMITTED, max_length=40)
    user_repository = ForeignKey(UserRepository, null=True, on_delete=CASCADE)
    uploadDate = DateTimeField(blank=False, null=True)
    type_mask = CharField(choices=TypeMasks._CHOICES, default=TypeMasks.GENERATED_FROM_SHAPEFILES, max_length=50)
    aos_id = IntegerField('ID Region of Interest', null=True, blank=True)
    wkt_bounds = CharField(max_length=300, blank=False, null=True)
    small_name = CharField(max_length=100, default="Unnamed")  # workspaceAA-aosBB-leakCC???

    generated_from_ids = CharField(max_length=300, blank=False, null=True, editable=False)

    def __str__(self):              # __unicode__ on Python 2
        return self.name


class UserRepositoryLeakDetectionMaskRaster(models_base.Name):
    user_repository_mask = ForeignKey(UserRepositoryLeakDetectionMask, null=True, on_delete=CASCADE)
    maskRasterLayer = OneToOneField(raster_models.RasterLayer, on_delete=CASCADE, blank=True, null=True)

    def __str__(self):              # __unicode__ on Python 2
        return self.name
