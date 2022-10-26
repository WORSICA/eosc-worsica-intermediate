from django.conf.urls import url

from django.http import HttpResponseRedirect
from . import views

urlpatterns = [
    url(r'^$', lambda r: HttpResponseRedirect('job_submissions')),

    # job submissions
    url(r'^job_submissions/create$', views.create_job_submission, name='create_job_submission'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)$', views.show_job_submission, name='show_job_submission'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/list_imagesets$', views.list_job_submission_imagesets, name='list_job_submission_imagesets'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/details$', views.show_job_submission_details, name='show_job_submission_details'),

    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/dataverses$', views.list_job_submission_dataverse, name='list_job_submission_dataverse'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/dataverses/submit$', views.submit_job_submission_dataverse, name='submit_job_submission_dataverse'),

    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/edit$', views.edit_job_submission, name='edit_job_submission'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/restart$', views.restart_job_submission, name='restart_job_submission'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/update$', views.update_job_submission, name='update_job_submission'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/processimageset/(?P<process_imageset_id>[0-9]+)/download_products$',
        views.download_job_submission_products, name='download_job_submission_products'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/averageprocessimageset/(?P<process_imageset_id>[0-9]+)/download_products$',
        views.download_job_submission_products_climatology, name='download_job_submission_products_climatology'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/clone_cleanup_all$', views.clone_cleanup_all_job_submission_shapelines, name='clone_cleanup_all_job_submission_shapelines'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/processimageset/(?P<process_imageset_id>[0-9]+)/coastline/(?P<shp_mpis_id>[0-9]+)$',
        views.show_job_submission_shapeline, name='show_job_submission_shapeline'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/processimageset/(?P<process_imageset_id>[0-9]+)/coastline/(?P<shp_mpis_id>[0-9]+)/clone_cleanup$',
        views.clone_cleanup_job_submission_shapeline, name='clone_cleanup_job_submission_shapeline'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/processimageset/(?P<process_imageset_id>[0-9]+)/coastline/(?P<shp_mpis_id>[0-9]+)/delete_cleanup$',
        views.delete_cleanup_job_submission_shapeline, name='delete_cleanup_job_submission_shapeline'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/processimageset/(?P<process_imageset_id>[0-9]+)/coastline/(?P<shp_mpis_id>[0-9]+)/download_cleanup$',
        views.download_cleanup_job_submission_shapeline, name='download_cleanup_job_submission_shapeline'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/processimageset/(?P<process_imageset_id>[0-9]+)/(?P<raster_type>[\w+]+)/create_threshold$',
        views.create_threshold_job_submission, name='create_threshold_job_submission'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/processimageset/(?P<process_imageset_id>[0-9]+)/(?P<raster_type>[\w+]+)/download_threshold$',
        views.download_threshold_job_submission, name='download_threshold_job_submission'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/processimageset/(?P<process_imageset_id>[0-9]+)/(?P<raster_type>[\w+]+)/(?P<raster_id>[0-9]+)$',
        views.show_job_submission_raster, name='show_job_submission_raster'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/processimageset/(?P<process_imageset_id>[0-9]+)/(?P<raster_type>[\w+]+)/(?P<raster_id>[0-9]+)/show_histogram$',
        views.show_job_submission_raster_histogram, name='show_job_submission_raster_histogram'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/averageprocessimageset/(?P<process_imageset_id>[0-9]+)/(?P<raster_type>[\w+]+)/(?P<raster_id>[0-9]+)$',
        views.show_job_submission_raster_climatology, name='show_job_submission_raster_climatology'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/generatetopography/(?P<process_gt_id>[0-9]+)/(?P<raster_type>[\w+]+)/(?P<raster_id>[0-9]+)$',
        views.show_job_submission_topography_raster, name='show_job_submission_topography_raster'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/generatetopography/(?P<process_gt_id>[0-9]+)/download_topography$',
        views.download_job_submission_topography, name='download_job_submission_topography'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/generatetopography/(?P<process_gt_id>[0-9]+)/delete_topography$',
        views.delete_job_submission_topography, name='delete_job_submission_topography'),

    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/run$', views.run_job_submission, name='run_job_submission'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/stop$', views.stop_job_submission, name='stop_job_submission'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/delete$', views.delete_job_submission, name='delete_job_submission'),

    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/create$', views.create_leak_detection, name='create_leak_detection'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)$', views.get_leak_detection, name='get_leak_detection'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/run$', views.run_job_submission_leak_detection, name='run_job_submission_leak_detection'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/run_identify_leaks$',
        views.run_job_submission_leak_detection_identify_leaks, name='run_job_submission_leak_detection_identify_leaks'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/get_user_leak_points$',
        views.get_user_leak_points_leak_detection, name='get_user_leak_points_leak_detection'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/save_user_leak_points$',
        views.save_user_leak_points_leak_detection, name='save_user_leak_points_leak_detection'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/show_user_leak_point$',
        views.show_user_leak_point_leak_detection, name='show_user_leak_point_leak_detection'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/delete_a_user_leak_point$',
        views.delete_a_user_leak_point_leak_detection, name='delete_a_user_leak_point_leak_detection'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/delete_user_leak_points$',
        views.delete_user_leak_points_leak_detection, name='delete_user_leak_points_leak_detection'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/show_leak_points$',
        views.show_leak_points_leak_detection, name='show_leak_points_leak_detection'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/edit_user_leak_point$',
        views.edit_user_leak_point_leak_detection, name='edit_user_leak_point_leak_detection'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/download_user_leak_point$',
        views.download_user_leak_point_leak_detection, name='download_user_leak_point_leak_detection'),
    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/clone_user_leak_points$',
        views.clone_user_leak_points_leak_detection, name='clone_user_leak_points_leak_detection'),

    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/edit$', views.edit_leak_detection, name='edit_leak_detection'),
    url(
        r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/processimageset/(?P<process_imageset_id>[0-9]+)/(?P<determine_leak>[\w+]+)/(?P<leak_detection_out_type>[\w+]+)/(?P<raster_type>[\w+]+)/(?P<raster_id>[0-9]+)$',
        views.show_job_submission_raster_leakdetection,
        name='show_job_submission_raster_leakdetection'),
    url(
        r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/processimageset/(?P<process_imageset_id>[0-9]+)/(?P<determine_leak>[\w+]+)/(?P<leak_detection_out_type>[\w+]+)/(?P<raster_type>[\w+]+)/(?P<raster_id>[0-9]+)/leak_points/show$',
        views.show_job_submission_leakdetection_leakpoints,
        name='show_job_submission_leakdetection_leakpoints'),
    url(
        r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/processimageset/(?P<process_imageset_id>[0-9]+)/(?P<determine_leak>[\w+]+)/(?P<leak_detection_out_type>[\w+]+)/(?P<raster_type>[\w+]+)/(?P<raster_id>[0-9]+)/leak_points/download$',
        views.download_job_submission_leakdetection_leakpoints,
        name='download_job_submission_leakdetection_leakpoints'),
    url(
        r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/processimageset/(?P<process_imageset_id>[0-9]+)/(?P<determine_leak>[\w+]+)/(?P<leak_detection_out_type>[\w+]+)/(?P<raster_type>[\w+]+)/(?P<raster_id>[0-9]+)/leak_points/(?P<leak_point_id>[0-9]+)/change_visibility$',
        views.change_job_submission_leakdetection_leakpoint,
        name='change_job_submission_leakdetection_leakpoint'),
    url(
        r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/ucprocessimageset/(?P<ucprocess_imageset_id>[0-9]+)/(?P<determine_leak>[\w+]+)/(?P<leak_detection_out_type>[\w+]+)/(?P<raster_type>[\w+]+)/(?P<raster_id>[0-9]+)$',
        views.show_job_submission_raster_leakdetection_uc,
        name='show_job_submission_raster_leakdetection_uc'),
    url(
        r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/ucprocessimageset/(?P<ucprocess_imageset_id>[0-9]+)/(?P<determine_leak>[\w+]+)/(?P<leak_detection_out_type>[\w+]+)/(?P<raster_type>[\w+]+)/(?P<raster_id>[0-9]+)/leak_points/show$',
        views.show_job_submission_leakdetection_leakpoints_uc,
        name='show_job_submission_leakdetection_leakpoints_uc'),
    url(
        r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/ucprocessimageset/(?P<ucprocess_imageset_id>[0-9]+)/(?P<determine_leak>[\w+]+)/(?P<leak_detection_out_type>[\w+]+)/(?P<raster_type>[\w+]+)/(?P<raster_id>[0-9]+)/leak_points/download$',
        views.download_job_submission_leakdetection_leakpoints_uc,
        name='download_job_submission_leakdetection_leakpoints_uc'),
    url(
        r'^job_submissions/(?P<job_submission_id>[0-9]+)/leak_detections/(?P<leak_detection_id>[0-9]+)/ucprocessimageset/(?P<ucprocess_imageset_id>[0-9]+)/(?P<determine_leak>[\w+]+)/(?P<leak_detection_out_type>[\w+]+)/(?P<raster_type>[\w+]+)/(?P<raster_id>[0-9]+)/leak_points/(?P<leak_point_id>[0-9]+)/change_visibility$',
        views.change_job_submission_leakdetection_leakpoint_uc,
        name='change_job_submission_leakdetection_leakpoint_uc'),

    url(r'^job_submissions/(?P<job_submission_id>[0-9]+)/generate_topography$', views.generate_topography, name='generate_topography'),

    # user repository
    url(r'^user_repository/user(?P<user_id>[0-9]+)/list$', views.list_user_repository_by_roi, name='list_user_repository_by_roi'),

    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/show$', views.show_user_repository, name='show_user_repository'),

    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/geometries/preview$', views.preview_user_repository_geometry, name='preview_user_repository_geometry'),
    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/geometries/upload$', views.upload_user_repository_geometry, name='upload_user_repository_geometry'),
    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/geometries/(?P<geometry_id>[0-9]+)$', views.show_user_repository_geometry, name='show_user_repository_geometry'),
    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/geometries/(?P<geometry_id>[0-9]+)/delete$', views.delete_user_repository_geometry, name='delete_user_repository_geometry'),

    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/masks/upload$', views.upload_user_repository_mask, name='upload_user_repository_mask'),
    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/masks/(?P<mask_id>[0-9]+)/(?P<raster_id>[0-9]+)$', views.show_user_repository_mask, name='show_user_repository_mask'),
    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/masks/(?P<mask_id>[0-9]+)/delete$', views.delete_user_repository_mask, name='delete_user_repository_mask'),

    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/leaks/upload$', views.upload_user_repository_leak, name='upload_user_repository_leak'),
    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/leaks/(?P<leak_id>[0-9]+)$', views.show_user_repository_leak, name='show_user_repository_leak'),
    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/leaks/(?P<leak_id>[0-9]+)/delete$', views.delete_user_repository_leak, name='delete_user_repository_leak'),

    url(r'^probe_sea_tides$', views.probe_sea_tides, name='probe_sea_tides'),

    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/imagesets$', views.list_user_repository_imageset, name='list_user_repository_imageset'),
    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/imagesets/upload$', views.upload_user_repository_imageset, name='upload_user_repository_imageset'),
    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/imagesets/(?P<imageset_id>[0-9]+)$', views.show_user_repository_imageset, name='show_user_repository_imageset'),
    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/imagesets/(?P<imageset_id>[0-9]+)/show_thumbnail$',
        views.show_thumbnail_user_repository_imageset, name='show_thumbnail_user_repository_imageset'),
    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/imagesets/(?P<imageset_id>[0-9]+)/edit$', views.edit_user_repository_imageset, name='edit_user_repository_imageset'),
    url(r'^user_repository/user(?P<user_id>[0-9]+)/(?P<service_type>[\w+]+)/imagesets/(?P<imageset_id>[0-9]+)/delete$', views.delete_user_repository_imageset, name='delete_user_repository_imageset'),

]
