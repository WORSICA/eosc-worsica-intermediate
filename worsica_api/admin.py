from . import models
from django.contrib import admin
from worsica_web_intermediate import settings
from . import logger

worsica_logger = logger.init_logger('WORSICA-Intermediate.Admin', settings.LOG_PATH)
worsica_logger.info('worsica_api.admin')


@admin.register(models.ShapelineMPIS)
class ShapelineMPISAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'get_source_type', 'get_jobsub', 'get_imageset', 'state', 'isFromManualThreshold', )
    readonly_fields = ('id', 'name', 'small_name', 'polygons_geojson', 'isFromManualThreshold', )

    def has_add_permission(self, request):
        return False


@admin.register(models.Shapeline)
class ShapelineAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', )

    def has_add_permission(self, request):
        return False


class ProcessImageSetRasterAdmin(admin.ModelAdmin):
    list_display = ('processImageSet', 'name', 'indexType', 'rasterLayer', 'autoThresholdValue',)
    list_filter = ('processImageSet', 'indexType',)

    def has_add_permission(self, request):
        return False


class ProcessImageSetInline(admin.TabularInline):
    model = models.ProcessImageSet
    extra = 0
    fields = ('id', 'name', 'small_name', 'uuid', 'convertToL2A', 'processState',)
    readonly_fields = ('id', 'name', 'small_name', 'uuid', )
    ordering = ('-small_name', )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class GenerateTopographyMapRasterAdmin(admin.ModelAdmin):
    list_display = ('generateTopographyMap', 'name', 'indexType', 'rasterLayer',)
    list_filter = ('generateTopographyMap', 'indexType',)

    def has_add_permission(self, request):
        return False


class GenerateTopographyMapInline(admin.TabularInline):
    model = models.GenerateTopographyMap
    extra = 0
    fields = ('id', 'name', 'small_name', 'processState', 'generateTopographyFrom',)
    readonly_fields = ('id', 'name', 'small_name', 'generateTopographyFrom', )
    ordering = ('-small_name', )

    def has_add_permission(self, request):
        return False


@admin.register(models.ImageSetThresholdRaster)
class ImageSetThresholdRasterAdmin(admin.ModelAdmin):
    list_display = ('imageSetThreshold', 'name', 'indexType', 'rasterLayer',)
    list_filter = ('imageSetThreshold', 'indexType',)

    def has_add_permission(self, request):
        return False


@admin.register(models.ImageSetThreshold)
class ImageSetThresholdAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'small_name', 'state',)

    def has_add_permission(self, request):
        return False


@admin.register(models.MergedProcessImageSetRaster)
class MergedProcessImageSetRasterAdmin(admin.ModelAdmin):
    list_display = ('processImageSet', 'name', 'indexType', 'rasterLayer', 'autoThresholdValue', 'clone_cleanup_small_name', 'isFromManualThreshold',)
    list_filter = ('indexType',)

    def has_add_permission(self, request):
        return False


class MergedProcessImageSetInline(admin.TabularInline):
    model = models.MergedProcessImageSet
    extra = 0
    fields = ('id', 'name', 'small_name', 'isVirtual', 'processState', 'procImageSet_ids', 'interpolationImageSet_ids', 'sensingDate', )
    readonly_fields = ('id', 'name', 'small_name', 'isVirtual', 'procImageSet_ids', 'interpolationImageSet_ids', 'sensingDate', )
    ordering = ('-sensingDate', )

    def has_add_permission(self, request):
        return False


@admin.register(models.AverageMergedProcessImageSetRaster)
class AverageMergedProcessImageSetRasterAdmin(admin.ModelAdmin):
    list_display = ('amprocessImageSet', 'name', 'indexType', 'rasterLayer',)
    list_filter = ('amprocessImageSet', 'indexType',)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class AverageMergedProcessImageSetInline(admin.TabularInline):
    model = models.AverageMergedProcessImageSet
    extra = 0
    fields = ('id', 'name', 'small_name', 'processState', 'mergedprocImageSet_ids', 'date', )
    readonly_fields = ('id', 'name', 'small_name', 'mergedprocImageSet_ids', 'date', )
    ordering = ('-date', )

    def has_add_permission(self, request):
        return False


@admin.register(models.MergedProcessImageSetForLeakDetectionRasterLeakPoints)
class MergedProcessImageSetForLeakDetectionRasterLeakPointsAdmin(admin.ModelAdmin):
    list_display = ('id', 'second_deriv_raster', 'xCoordinate', 'yCoordinate', 'pixelValue', 'showLeakPoint')
    list_filter = ('second_deriv_raster__processImageSet',)

    def has_add_permission(self, request):
        return False


@admin.register(models.UserRepositoryLeakDetectionLeakPoints)
class UserRepositoryLeakDetectionLeakPointsAdmin(admin.ModelAdmin):
    list_display = ('id', 'user_repository_leak', 'xCoordinate', 'yCoordinate', 'pixelValue', 'showLeakPoint')
    list_filter = ('user_repository_leak__user_repository',)  # = ('id', 'name', 'xCoordinate', 'yCoordinate', 'pixelValue', 'showLeakPoint')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class MergedProcessImageSetForLeakDetectionRasterInline(admin.TabularInline):
    model = models.MergedProcessImageSetForLeakDetectionRaster
    extra = 0
    fields = ('id', 'ldType', )
    readonly_fields = ('id', 'ldType', )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class MergedProcessImageSetForLeakDetectionMaskInline(admin.TabularInline):
    model = models.MergedProcessImageSetForLeakDetectionMask
    extra = 0
    fields = ('id', 'wkt_bounds', 'small_name', 'generated_from_ids', )
    readonly_fields = ('id', 'wkt_bounds', 'small_name', 'generated_from_ids', )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPointsInline(admin.TabularInline):
    model = models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPoints
    extra = 0
    fields = ('id', 'xCoordinate', 'yCoordinate', 'pixelValue', 'showLeakPoint', )
    readonly_fields = ('id', 'xCoordinate', 'yCoordinate', 'pixelValue', 'showLeakPoint', )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSaved)
class MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedAdmin(admin.ModelAdmin):
    inlines = [
        MergedProcessImageSetForLeakDetectionRasterLeakPointsUserSavedPointsInline,
    ]
    list_display = ('id', 'leak_detection', 'name', 'small_name',)
    list_filter = ('leak_detection',)

    def has_add_permission(self, request):
        return False


@admin.register(models.MergedProcessImageSetForLeakDetection)
class MergedProcessImageSetForLeakDetectionAdmin(admin.ModelAdmin):
    inlines = [
        MergedProcessImageSetForLeakDetectionRasterInline,
        MergedProcessImageSetForLeakDetectionMaskInline,
    ]
    list_display = ('id', 'indexType', 'ucprocessImageSet', 'processImageSet', 'name', 'small_name', 'start_processing_second_deriv_from', 'processState')
    list_filter = ('indexType', 'ucprocessImageSet', 'processImageSet', 'start_processing_second_deriv_from')

    def get_job_submission(self, obj):
        if obj.jobSubmission:
            return obj.jobSubmission.name

    def has_add_permission(self, request):
        return False


@admin.register(models.JobSubmission)
class JobSubmissionAdmin(admin.ModelAdmin):
    inlines = [
        ProcessImageSetInline,
        MergedProcessImageSetInline,
        AverageMergedProcessImageSetInline,
        GenerateTopographyMapInline,
    ]
    list_display = ('name', 'service', 'user_id', 'aos_id', 'simulation_id', 'provider', 'state')

    def has_add_permission(self, request):
        return False


@admin.register(models.DataverseSubmission)
class DataverseSubmissionAdmin(admin.ModelAdmin):
    list_display = ('name', 'jobSubmission', 'doi', 'state', 'creationDate')

    def has_add_permission(self, request):
        return False


class UserChosenMergedProcessImageSetInline(admin.TabularInline):
    model = models.UserChosenMergedProcessImageSet
    extra = 0
    fields = ('id', 'name', 'small_name', 'processState', 'procImageSet_ids', 'sensingDate', )
    readonly_fields = ('id', 'name', 'small_name', 'procImageSet_ids', 'sensingDate', )
    ordering = ('-sensingDate', )

    def has_add_permission(self, request):
        return False


class UserChosenProcessImageSetInline(admin.TabularInline):
    model = models.UserChosenProcessImageSet
    extra = 0
    fields = ('id', 'name', 'small_name', 'uuid', 'convertToL2A', 'processState',)
    readonly_fields = ('id', 'name', 'small_name', 'uuid', )
    ordering = ('-small_name', )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.UserChosenProduct)
class UserChosenProductAdmin(admin.ModelAdmin):
    inlines = [
        UserChosenProcessImageSetInline,
        UserChosenMergedProcessImageSetInline,
    ]
    list_display = ('name', 'service', 'aos_id', 'provider', 'state')

    def has_add_permission(self, request):
        return False


@admin.register(models.RepositoryImageSets)
class RepositoryImageSetsAdmin(admin.ModelAdmin):
    list_display = ('name', 'state', 'provider', 'issueDate', 'expirationDate', 'managedByJobId', 'childRepositoryImageSet',)
    readonly_fields = ('issueDate', 'expirationDate', 'managedByJobId', 'childRepositoryImageSet',)

    def has_add_permission(self, request):
        return False


class UserRepositoryGeometryShapelineInline(admin.TabularInline):
    model = models.UserRepositoryGeometryShapeline
    extra = 0
    fields = ('id', 'name',)
    readonly_fields = ('id', 'name', )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.UserRepositoryGeometry)
class UserRepositoryGeometryAdmin(admin.ModelAdmin):
    inlines = [
        UserRepositoryGeometryShapelineInline
    ]
    list_display = ('user_repository', 'id', 'name', 'reference', 'state', 'uploadDate', 'service', 'wkt_bounds')
    readonly_fields = ('user_repository', 'id', 'name', 'reference', 'state', 'uploadDate', 'service', 'wkt_bounds')
    list_filter = ('user_repository',)

    def has_add_permission(self, request):
        return False


class UserRepositoryLeakDetectionMaskRasterInline(admin.TabularInline):
    model = models.UserRepositoryLeakDetectionMaskRaster
    extra = 0
    fields = ('id', 'name', 'maskRasterLayer')
    readonly_fields = ('id', 'name', 'maskRasterLayer', )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.UserRepositoryLeakDetectionMask)
class UserRepositoryLeakDetectionMaskAdmin(admin.ModelAdmin):
    inlines = [
        UserRepositoryLeakDetectionMaskRasterInline
    ]
    list_display = ('user_repository', 'id', 'name', 'reference', 'aos_id', 'state', 'uploadDate', 'type_mask', 'wkt_bounds', 'generated_from_ids')
    readonly_fields = ('user_repository', 'id', 'name', 'reference', 'aos_id', 'state', 'uploadDate', 'type_mask', 'wkt_bounds', 'generated_from_ids')
    list_filter = ('user_repository',)

    def has_add_permission(self, request):
        return False


@admin.register(models.UserRepositoryImageSets)
class UserRepositoryImageSetsAdmin(admin.ModelAdmin):

    list_display = ('user_repository', 'id', 'name', 'reference', 'state', 'issueDate', 'uploadDate', 'wkt_bounds', )
    readonly_fields = ('user_repository', 'id', 'name', 'reference', 'state', 'issueDate', 'uploadDate', 'wkt_bounds', )
    list_filter = ('user_repository',)


@admin.register(models.UserRepositoryLeakDetectionLeak)
class UserRepositoryLeakDetectionLeakAdmin(admin.ModelAdmin):
    list_display = ('user_repository', 'id', 'name', 'reference', 'state', 'uploadDate', 'wkt_bounds',)
    readonly_fields = ('user_repository', 'id', 'name', 'reference', 'state', 'uploadDate', 'wkt_bounds', )
    list_filter = ('user_repository',)

    def has_add_permission(self, request):
        return False
