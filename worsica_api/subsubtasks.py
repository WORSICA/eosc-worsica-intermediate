from django.core.files import File

import worsica_api.models as worsica_api_models
import raster.models as raster_models
import json

import os

import requests
import zipfile

import traceback
import numpy as np
import cv2
from osgeo import gdal, ogr, osr

from django.contrib.gis.geos import GEOSGeometry

from worsica_web_intermediate import settings, nextcloud_access
from . import logger

worsica_logger = logger.init_logger('WORSICA-Intermediate.Views', settings.LOG_PATH)
ACTUAL_PATH = os.getcwd()


def _change_object_state(pi, state, ri=None):
    pi.state = state
    pi.save()


def _normalize_wkt(wkt):
    wkt = wkt.replace('\"', '').replace('; ', ';')  # remove '"'s, and space in ';'
    wkt = wkt.replace('((', ' ((')  # give some space
    return wkt

# GDAL 3 changed the way the coords are stored and displayed
# ending up messing the plans for me
# I could not change the coordinates directly on the features because they are tuples,
# so the only way is to change coordinates from the wkt text in roi_polygon
# instead of POLYGON ((x1,y1),(x2,y2),...)
# change it to POLYGON ((y1,x1),(y2,x2),...)


def _swap_coordinates_geojson_aos_polygon(roi_polygon):
    r0 = roi_polygon.replace('POLYGON', '').replace(' ((', '').replace('))', '').replace(', ', ',')
    r1 = r0.split(',')
    r2 = []
    for r1b in r1:
        r1c = r1b.split(' ')
        r2.append(' '.join([r1c[1], r1c[0]]))
    roi_polygon = 'POLYGON ((' + ', '.join(r2) + '))'
    return roi_polygon

# Convert shp to a raster mask


def _shp_to_raster_mask(user_id, mpis):
    # TODO: assertion for missing shp
    # 1: load shp from DB
    # 2: convert to raster
    try:
        aos_id = mpis.jobSubmission.aos_id
        simulation_id = mpis.jobSubmission.simulation_id
        leak_detection_id = mpis.id
        js_exec_args = json.loads(mpis.jobSubmission.exec_arguments)
        roi_polygon = js_exec_args['roi']
        ld_exec_args = json.loads(mpis.exec_arguments)
        mask_width = int(ld_exec_args['step3LeakDetection']['mask_width'])  # pixel resolution
        geometries_id = ld_exec_args['step3LeakDetection']['geometry_id']
        worsica_logger.info('[_shp_to_raster_mask]: geometries_id=' + str(geometries_id))

        FILE_NAME_MASK = 'ld' + str(leak_detection_id) + '_mask'
        user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
        ldmask, ldmask_created = worsica_api_models.MergedProcessImageSetForLeakDetectionMask.objects.get_or_create(
            processImageSet=mpis, name=FILE_NAME_MASK, reference=FILE_NAME_MASK)
        ldmask.generated_from_ids = ','.join(str(e) for e in geometries_id)
        ldmask.save()
        # delete mask raster if any
        worsica_api_models.MergedProcessImageSetForLeakDetectionMaskRaster.objects.filter(ld_mask=ldmask).delete()

        _change_object_state(ldmask, 'generating-mask')

        pixel_size = 10
        nodata_val = 0  # -9999
        export_srid = 3857

        roi_polygon = _swap_coordinates_geojson_aos_polygon(roi_polygon)

        GEOJSON_AOS_POLYGON = GEOSGeometry("SRID=4326;" + roi_polygon)
        print(GEOJSON_AOS_POLYGON.extent)
        worsica_logger.info('[_shp_to_raster_mask]: Start searching geometry features that match with AoS boundaries')
        geometries = worsica_api_models.UserRepositoryGeometry.objects.filter(user_repository=user_repository, id__in=geometries_id)
        features = ogr.Geometry(ogr.wkbMultiLineString)
        if len(geometries) > 0:
            for geom in geometries:
                geomfeatures = worsica_api_models.UserRepositoryGeometryShapeline.objects.filter(user_repository_geom=geom)
                for geomfeature in geomfeatures:
                    # This step is required in order to generate properly the raster
                    # DB shapelines are MultiLineString, that should be LineString, and to be loaded into the mother Geometry MultiLineString
                    if (GEOJSON_AOS_POLYGON.contains(geomfeature.mls)):
                        s = ogr.Geometry(ogr.wkbLineString)
                        for k in geomfeature.mls.coords[0]:
                            s.AddPoint(k[0], k[1])
                        inSpatialRef = osr.SpatialReference()
                        inSpatialRef.ImportFromEPSG(geomfeature.mls.srid)

                        # output SpatialReference
                        outSpatialRef = osr.SpatialReference()
                        outSpatialRef.ImportFromEPSG(export_srid)
                        # apply conversion to 3857 EPSG
                        s.Transform(osr.CoordinateTransformation(inSpatialRef, outSpatialRef))
                        features.AddGeometry(s)

            # GetEnvelope will provide the bounds of the features' shapefile
            x_min, x_max, y_min, y_max = features.GetEnvelope()
            print(x_min, x_max, y_min, y_max)

            # generate a volatile ogr vector to store the features and to be able to run rasterizelayer
            worsica_logger.info('[_shp_to_raster_mask]: generate a volatile ogr vector to store the features and to be able to run rasterizelayer')
            outdriver = ogr.GetDriverByName('MEMORY')
            source = outdriver.CreateDataSource('memData')
            source_layer = source.CreateLayer("features", geom_type=ogr.wkbMultiLineString)
            # Get the output Layer's Feature Definition
            # create a new feature
            outFeature = ogr.Feature(source_layer.GetLayerDefn())
            # Set new geometry
            outFeature.SetGeometry(features)
            # Add new feature to output Layer
            source_layer.CreateFeature(outFeature)

            # Create the raster with the size of the AoS, and fill it with zeros
            # generate a volatile raster that will be used
            worsica_logger.info('[_shp_to_raster_mask]: generate a volatile raster that will be used')
            GEOJSON_AOS_POLYGON_TRANSF = GEOJSON_AOS_POLYGON.transform(export_srid, clone=True)
            ldmask.wkt_bounds = str(GEOJSON_AOS_POLYGON_TRANSF)
            ldmask.save()
            px_min, py_min, px_max, py_max = GEOJSON_AOS_POLYGON_TRANSF.extent
            x_res = int((px_max - px_min) / pixel_size)
            y_res = int((py_max - py_min) / pixel_size)
            print(x_res, y_res)
            mem_raster = gdal.GetDriverByName('MEM').Create('', x_res, y_res, 1, gdal.GDT_Float32)  # raster with size of the AoS in pixels
            mem_raster.SetGeoTransform((px_min, pixel_size, 0, py_max, 0, -pixel_size))  # geotransform from the features
            mem_band = mem_raster.GetRasterBand(1)
            mem_band.Fill(nodata_val)  # fill with 0
            mem_band.SetNoDataValue(nodata_val)  # if nodata, set zero too

            worsica_logger.info('[_shp_to_raster_mask]: run gdal.rasterize')
            # http://gdal.org/gdal__alg_8h.html#adfe5e5d287d6c184aab03acbfa567cb1
            # http://gis.stackexchange.com/questions/31568/gdal-rasterizelayer-doesnt-burn-all-polygons-to-raster
            # all_touched assures that even a tiny bit of the shapefile crosses on a pixel, it's marked with a burned value
            gdal.RasterizeLayer(mem_raster, [1], source_layer, None, None, [1], ['ALL_TOUCHED=TRUE'])
            # burn the shapeline as pixel value 1 on the raster

            worsica_logger.info('[_shp_to_raster_mask]: open the raster and apply dilatation')
            img = mem_raster.ReadAsArray()
            img2 = np.where(img == 0, 0., 255.)
            KernelSize, Iter = round(mask_width / 2), 3  # kernel size was before: 5, so 10/2 = 5?
            kernel = np.ones((KernelSize, KernelSize), np.uint8)
            dilation = cv2.dilate(img2, kernel, iterations=Iter)

            # download zip file
            auth = (nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD)
            PATH_TO_PRODUCTS = '/tmp/waterleak/user' + str(user_id) + '/roi' + str(aos_id) + '/simulation' + str(simulation_id) + '/ld' + str(leak_detection_id)  # +'/masks/waterleak'
            ZIP_FILE = 'waterleak-user' + str(user_id) + '-roi' + str(aos_id) + '-s' + str(simulation_id) + '-ld' + str(leak_detection_id) + '-final-products'
            ZIP_FILE_PATH = PATH_TO_PRODUCTS + '/' + ZIP_FILE + '.zip'
            worsica_logger.info('[_shp_to_raster_mask]: download file to tmp')
            nc_path = nextcloud_access.NEXTCLOUD_URL_PATH + '/waterleak/user' + str(user_id) + '/roi' + str(aos_id) + '/simulation' + str(simulation_id) + '/' + ZIP_FILE + '.zip'
            r = requests.get(nc_path, auth=auth)
            if r.status_code == 200:
                os.makedirs(PATH_TO_PRODUCTS, exist_ok=True)
                with open(ZIP_FILE_PATH, 'wb') as f:
                    f.write(r.content)
            zf = zipfile.ZipFile(ZIP_FILE_PATH, 'r')
            zf.extractall('/')
            # generate file
            createTIFOutputImage(FILE_NAME_MASK, PATH_TO_PRODUCTS, x_res, y_res, mem_raster.GetGeoTransform(), export_srid, dilation)

            # add file and upload to nextcloud
            TIF_FILE_PATH = PATH_TO_PRODUCTS + '/' + FILE_NAME_MASK + '.tif'
            ALT_TIF_FILE_PATH = '/waterleak/user' + str(user_id) + '/roi' + str(aos_id) + '/simulation' + str(simulation_id) + '/ld' + str(leak_detection_id) + '/' + FILE_NAME_MASK + '.tif'
            print('[_shp_to_raster_mask]: zip all files')
            zipfile.ZipFile(ZIP_FILE_PATH, mode='a').write(TIF_FILE_PATH, arcname=ALT_TIF_FILE_PATH, compress_type=zipfile.ZIP_DEFLATED)
            print('[_shp_to_raster_mask]: start upload file directly to nextcloud')
            files = {'myfile': open(ZIP_FILE_PATH, 'rb')}
            r = requests.request('PUT', nc_path, files=files, auth=auth)
            if (r.status_code == 201 or r.status_code == 204):
                print('[_shp_to_raster_mask]: upload successful')
                os.remove(ZIP_FILE_PATH)

            # store it to raster
            wrapped_file = open(TIF_FILE_PATH, 'rb')
            nameRl = FILE_NAME_MASK
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
            ldmaskraster, ldmaskraster_created = worsica_api_models.MergedProcessImageSetForLeakDetectionMaskRaster.objects.get_or_create(
                ld_mask=ldmask, name=ldmask.name, reference=ldmask.reference)
            ldmaskraster.maskRasterLayer = rl
            ldmaskraster.save()

            _change_object_state(ldmask, 'generated-mask')
            print('OK')
            return 'generated-mask'
        else:
            print('ERROR!!!')
            print('No features found.')
            _change_object_state(ldmask, 'error-generating-mask')
            return 'error-generating-mask'

    except Exception as e:
        print('ERROR!!!')
        print(traceback.format_exc())
        _change_object_state(ldmask, 'error-generating-mask')
        return 'error-generating-mask'


# Filter leaks by one mask or more
def _start_filter_leak_by_raster_mask(user_id, roi_polygon):
    try:
        pixel_size = 10  # pixel resolution
        nodata_val = 0  # -9999
        export_srid = 3857

        print('[_start_filter_leak_by_raster_mask]: get roi extent')
        roi_polygon = _swap_coordinates_geojson_aos_polygon(roi_polygon)
        GEOJSON_AOS_POLYGON = GEOSGeometry("SRID=4326;" + roi_polygon)
        print(GEOJSON_AOS_POLYGON.extent)
        GEOJSON_AOS_POLYGON_TRANSF = GEOJSON_AOS_POLYGON.transform(export_srid, clone=True)
        print(GEOJSON_AOS_POLYGON_TRANSF.extent)

        # get a list of available masks, and check them by bounding boxes
        print('[_start_filter_leak_by_raster_mask]: get a list of available masks, and check them by bounding boxes')
        aosmasks = []
        user_repository = worsica_api_models.UserRepository.objects.get(portal_user_id=int(user_id))
        masks = worsica_api_models.UserRepositoryLeakDetectionMask.objects.filter(user_repository=user_repository)
        for mask in masks:
            print('.................')
            print(mask.name)
            GEOJSON_MASK_POLYGON = GEOSGeometry(mask.wkt_bounds)
            GEOJSON_MASK_POLYGON_TRANSF = GEOJSON_MASK_POLYGON.transform(export_srid, clone=True)

            print(GEOJSON_MASK_POLYGON_TRANSF.extent)
            if (GEOJSON_AOS_POLYGON_TRANSF.intersects(GEOJSON_MASK_POLYGON_TRANSF)):
                print('[_start_filter_leak_by_raster_mask]: found a possible mask candidate (maskid=' + str(mask.id) + ')')
                aosmasks.append(mask)
            print('.................')
        print(aosmasks)

        if len(aosmasks) > 0:
            # create a memory raster with the size of the AoS
            print('[_start_filter_leak_by_raster_mask]: create a memory raster with the size of the AoS')
            px_min, py_min, px_max, py_max = GEOJSON_AOS_POLYGON_TRANSF.extent
            print(px_min, px_max, py_min, py_max)

            x_res = int((px_max - px_min) / pixel_size)
            y_res = int((py_max - py_min) / pixel_size)
            print(x_res, y_res)

            mem_raster = gdal.GetDriverByName('MEM').Create('', x_res, y_res, 1, gdal.GDT_Float32)  # raster with size of the AoS in pixels
            mem_raster.SetGeoTransform((px_min, pixel_size, 0, py_max, 0, -pixel_size))  # geotransform from the features
            mem_band = mem_raster.GetRasterBand(1)
            mem_band.Fill(nodata_val)  # fill with 0
            mem_band.SetNoDataValue(nodata_val)  # if nodata, set zero too
            auxData = mem_band.ReadAsArray()  # mem_raster
            print(auxData)

            # start fetching the available masks
            for aosmask in aosmasks:
                print('[_start_filter_leak_by_raster_mask]: mask ' + aosmask.name)
                print('[_start_filter_leak_by_raster_mask]: download from nextcloud')
                myfilename_noext = aosmask.name
                tmppath = '/tmp/user_repository/user' + str(user_id) + '/masks/waterleak'
                ziptmppath = tmppath + '/' + myfilename_noext + '.zip'
                nc_path = nextcloud_access.NEXTCLOUD_URL_PATH + '/user_repository/user' + str(user_id) + '/masks/waterleak/' + myfilename_noext + '.zip'
                auth = (nextcloud_access.NEXTCLOUD_USER, nextcloud_access.NEXTCLOUD_PWD)
                r = requests.get(nc_path, auth=auth)
                if r.status_code == 200:
                    os.makedirs(tmppath, exist_ok=True)
                    with open(ziptmppath, 'wb') as f:
                        f.write(r.content)
                zf = zipfile.ZipFile(ziptmppath, 'r')
                zf.extractall(tmppath)
                if aosmask.type_mask == 'generated_from_shps':
                    f = tmppath + '/' + tmppath + '/' + myfilename_noext + '.tif'
                else:
                    f = tmppath + '/' + myfilename_noext + '.tif'
                print('-----open ' + f)
                raster = gdal.Open(f, gdal.GA_ReadOnly)
                # translate consists in loading the image to memory and set it to have the same window and size as the AoS
                # (fill it with no data values if mask does not cover the area)
                (uY, uX, lY, lX) = GEOJSON_AOS_POLYGON.extent
                print((uX, uY, lX, lY))
                ulx = min(uX, lX)
                uly = max(uY, lY)
                lrx = max(uX, lX)
                lry = min(uY, lY)
                print([ulx, uly, lrx, lry])
                rasterT = gdal.Translate('', raster, format='MEM',
                                         projWinSRS='EPSG:4326', projWin=[ulx, uly, lrx, lry], noData=nodata_val,
                                         width=x_res, height=y_res)
                if rasterT is not None:
                    data = rasterT.GetRasterBand(1).ReadAsArray()
                    # this is a way to 'merge' the masks uploaded by the user.
                    dataBoolMask = np.where(data == 0, False, True)
                    auxDataBoolMask = np.where(auxData == 0, False, True)
                    log_or = np.logical_or(dataBoolMask, auxDataBoolMask)
                    auxData[log_or] = 255
                raster, rasterT, data = None, None, None
            return auxData, [x_res, y_res], mem_raster.GetGeoTransform()
        else:
            return None, None, None
    except Exception as e:
        print('ERROR!!!')
        print(traceback.format_exc())
        return None, None, None

# for generation of tif outputs


def createTIFOutputImage(FILE_NAME, PATH_TO_PRODUCTS, maxW, maxH, gt, srid, c):
    print('createTIFOutputImage: ' + PATH_TO_PRODUCTS + '/' + FILE_NAME + '.tif')
    try:
        driver = gdal.GetDriverByName('GTiff')
        outRaster = driver.Create(PATH_TO_PRODUCTS + '/' + FILE_NAME + '.tif', maxW, maxH, 1, gdal.GDT_Float32)
        outRaster.SetGeoTransform((gt[0], gt[1], gt[2], gt[3], gt[4], gt[5]))
        outband = outRaster.GetRasterBand(1)
        outband.WriteArray(c)
        outRasterSRS = osr.SpatialReference()
        outRasterSRS.ImportFromEPSG(srid)
        outRaster.SetProjection(outRasterSRS.ExportToWkt())
        outband.FlushCache()
        outband = None
        outRaster = None
    except Exception as e:
        print('Error createTIFOutputImage: ' + str(e))
        print(traceback.print_exc())
