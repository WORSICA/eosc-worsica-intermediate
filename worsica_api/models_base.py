from django.db.models import (
    Model, CharField, SlugField, TextField
)
from worsica_web_intermediate import settings
from . import logger

worsica_logger = logger.init_logger('WORSICA-Intermediate.ModelsBase', settings.LOG_PATH)
worsica_logger.info('worsica_api.models_base')


class Name(Model):

    CHAR_FIELD_MAX_LENGTH = 200

    name = CharField(max_length=CHAR_FIELD_MAX_LENGTH)
    reference = SlugField(max_length=CHAR_FIELD_MAX_LENGTH)

    class Meta:
        abstract = True

    def __str__(self):
        return self.name


class Info(Model):

    description = TextField(blank=True)
    notes = TextField(blank=True)

    class Meta:
        abstract = True


class BlobTextTrait(Model):

    blob_text = TextField(blank=True)

    class Meta:
        abstract = True


class ImageSet(Name):
    CHAR_FIELD_MAX_LENGTH = 200

    filePath = CharField(max_length=CHAR_FIELD_MAX_LENGTH, default="Unnamed")
    uuid = CharField(max_length=CHAR_FIELD_MAX_LENGTH, default="Unnamed")

    class Meta:
        abstract = True


class IndexImageSet(Name):
    class IndexType:
        RGB = 'rgb'
        NDWI = 'ndwi'
        NDWI_BINARY = 'ndwi_binary'
        NDWI_BINARY_CE = 'ndwi_binary_closing_edge_detection'
        NDWI_CLEANUP_BINARY = 'ndwi_cleanup_binary'
        NDWI_CLEANUP_BINARY_CE = 'ndwi_cleanup_binary_closing_edge_detection'
        NDWI1 = 'ndwi1'
        NDWI1_BINARY = 'ndwi1_binary'
        NDWI1_BINARY_CE = 'ndwi1_binary_closing_edge_detection'
        NDWI1_CLEANUP_BINARY = 'ndwi1_cleanup_binary'
        NDWI1_CLEANUP_BINARY_CE = 'ndwi1_cleanup_binary_closing_edge_detection'
        NDWI2 = 'ndwi2'
        NDWI2_BINARY = 'ndwi2_binary'
        NDWI2_BINARY_CE = 'ndwi2_binary_closing_edge_detection'
        NDWI2_CLEANUP_BINARY = 'ndwi2_cleanup_binary'
        NDWI2_CLEANUP_BINARY_CE = 'ndwi2_cleanup_binary_closing_edge_detection'
        MNDWI = 'mndwi'
        MNDWI_BINARY = 'mndwi_binary'
        MNDWI_BINARY_CE = 'mndwi_binary_closing_edge_detection'
        MNDWI_CLEANUP_BINARY = 'mndwi_cleanup_binary'
        MNDWI_CLEANUP_BINARY_CE = 'mndwi_cleanup_binary_closing_edge_detection'
        MNDWI2 = 'mndwi2'
        MNDWI2_BINARY = 'mndwi2_binary'
        MNDWI2_BINARY_CE = 'mndwi2_binary_closing_edge_detection'
        MNDWI2_CLEANUP_BINARY = 'mndwi2_cleanup_binary'
        MNDWI2_CLEANUP_BINARY_CE = 'mndwi2_cleanup_binary_closing_edge_detection'
        NDFI = 'ndfi'
        NDFI_BINARY = 'ndfi_binary'
        NDFI_BINARY_CE = 'ndfi_binary_closing_edge_detection'
        NDFI_CLEANUP_BINARY = 'ndfi_cleanup_binary'
        NDFI_CLEANUP_BINARY_CE = 'ndfi_cleanup_binary_closing_edge_detection'
        NDMI = 'ndmi'
        NDMI_BINARY = 'ndmi_binary'
        NDMI_BINARY_CE = 'ndmi_binary_closing_edge_detection'
        NDMI_CLEANUP_BINARY = 'ndmi_cleanup_binary'
        NDMI_CLEANUP_BINARY_CE = 'ndmi_cleanup_binary_closing_edge_detection'
        AWEI = 'awei'
        AWEI_BINARY = 'awei_binary'
        AWEI_BINARY_CE = 'awei_binary_closing_edge_detection'
        AWEI_CLEANUP_BINARY = 'awei_cleanup_binary'
        AWEI_CLEANUP_BINARY_CE = 'awei_cleanup_binary_closing_edge_detection'
        AWEISH = 'aweish'
        AWEISH_BINARY = 'aweish_binary'
        AWEISH_BINARY_CE = 'aweish_binary_closing_edge_detection'
        AWEISH_CLEANUP_BINARY = 'aweish_cleanup_binary'
        AWEISH_CLEANUP_BINARY_CE = 'aweish_cleanup_binary_closing_edge_detection'

        _CHOICES = (
            (RGB, 'RGB'),
            (NDWI, 'NDWI'),
            (NDWI_BINARY, 'NDWI binary'),
            (NDWI_BINARY_CE, 'NDWI binary closing edge'),
            (NDWI_CLEANUP_BINARY, 'NDWI cleanup binary'),
            (NDWI_CLEANUP_BINARY_CE, 'NDWI cleanup binary closing edge'),
            (NDWI1, 'NDWI1'),
            (NDWI1_BINARY, 'NDWI1 binary'),
            (NDWI1_BINARY_CE, 'NDWI1 binary closing edge'),
            (NDWI1_CLEANUP_BINARY, 'NDWI1 cleanup binary'),
            (NDWI1_CLEANUP_BINARY_CE, 'NDWI1 cleanup binary closing edge'),
            (NDWI2, 'NDWI2'),
            (NDWI2_BINARY, 'NDWI2 binary'),
            (NDWI2_BINARY_CE, 'NDWI2 binary closing edge'),
            (NDWI2_CLEANUP_BINARY, 'NDWI2 cleanup binary'),
            (NDWI2_CLEANUP_BINARY_CE, 'NDWI2 cleanup binary closing edge'),
            (MNDWI, 'MNDWI'),
            (MNDWI_BINARY, 'MNDWI binary'),
            (MNDWI_BINARY_CE, 'MNDWI binary closing edge'),
            (MNDWI_CLEANUP_BINARY, 'MNDWI cleanup binary'),
            (MNDWI_CLEANUP_BINARY_CE, 'MNDWI cleanup binary closing edge'),
            (MNDWI2, 'MNDWI2'),
            (MNDWI2_BINARY, 'MNDWI2 binary'),
            (MNDWI2_BINARY_CE, 'MNDWI2 binary closing edge'),
            (MNDWI2_CLEANUP_BINARY, 'MNDWI2 cleanup binary'),
            (MNDWI2_CLEANUP_BINARY_CE, 'MNDWI2 cleanup binary closing edge'),
            (NDFI, 'NDFI'),
            (NDFI_BINARY, 'NDFI binary'),
            (NDFI_BINARY_CE, 'NDFI binary closing edge'),
            (NDFI_CLEANUP_BINARY, 'NDFI cleanup binary'),
            (NDFI_CLEANUP_BINARY_CE, 'NDFI cleanup binary closing edge'),
            (NDMI, 'NDMI'),
            (NDMI_BINARY, 'NDMI binary'),
            (NDMI_BINARY_CE, 'NDMI binary closing edge'),
            (NDMI_CLEANUP_BINARY, 'NDMI cleanup binary'),
            (NDMI_CLEANUP_BINARY_CE, 'NDMI cleanup binary closing edge'),
            (AWEI, 'AWEI'),
            (AWEI_BINARY, 'AWEI binary'),
            (AWEI_BINARY_CE, 'AWEI binary closing edge'),
            (AWEI_CLEANUP_BINARY, 'AWEI cleanup binary'),
            (AWEI_CLEANUP_BINARY_CE, 'AWEI cleanup binary closing edge'),
            (AWEISH, 'AWEISH'),
            (AWEISH_BINARY, 'AWEISH binary'),
            (AWEISH_BINARY_CE, 'AWEISH binary closing edge'),
            (AWEISH_CLEANUP_BINARY, 'AWEISH cleanup binary'),
            (AWEISH_CLEANUP_BINARY_CE, 'AWEISH cleanup binary closing edge'),
        )
    indexType = CharField(choices=IndexType._CHOICES, default=IndexType.MNDWI, max_length=120)

    class Meta:
        abstract = True
