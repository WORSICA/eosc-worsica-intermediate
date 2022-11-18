[![GitHub license](https://img.shields.io/github/license/WorSiCa/worsica-portal.svg?maxAge=2592000&style=flat-square)](https://github.com/WORSICA/worsica-intermediate/blob/master/LICENSE)
[![GitHub release](https://img.shields.io/github/release/WorSiCa/worsica-portal.svg?maxAge=3600&style=flat-square)](https://github.com/WORSICA/worsica-intermediate/releases/latest)
[![Build Status](https://jenkins.eosc-synergy.eu/buildStatus/icon?job=WORSICA%2Fworsica-intermediate%2Fdevelopment)](https://jenkins.eosc-synergy.eu/job/WORSICA/job/worsica-intermediate/job/development/)
# worsica-intermediate

WORSICA Web Intermediate Service

This is the core component that manages processing requests from the users by the web portal (worsica-frontend) and initializes the execution of the scripts from the processing (worsica-processing), through API REST.

## Features

- Django web admin interface with Celery task scheduler
- Ability to send processing requests to the GRID
- Uses django-raster module for loading rasters
- Functional tests

## Requirements

- worsica-essentials docker image
- RabbitMQ docker image
- PostgreSQL/PostGIS docker image
- Nextcloud docker image
- netcdf FES2014 files
- worsica-processing files

## Build

**NOTE: In order to build this image, you need to build the worsica-essentials docker image first, available at WORSICA/worsica-cicd repository.**

The Dockerfile.intermediate file provided at docker_intermediate/aio_v4, do:

```shell
cd docker_intermediate/aio_v4
docker build -t worsica/worsica-intermediate:development -f Dockerfile.intermediate .
```

## Configurations

Before running, first you need to config the following files:

```
worsica_web_intermediate/settings_sensitive.py: Django Settings
worsica_web_intermediate/nextcloud_access.py: Credentials and configs for Nextcloud
worsica_web_intermediate/dataverse_access.py: Credentials and configs for Dataverse
worsica_api/SSecrets.py: Credentials for Sentinel image search
```

We provided their _template files to copy them and set the respective file name above. For some cases, you need to create an user account in order to make it work.

## Execute

**NOTE: Assure that you already have all the requirements installed to run the worsica-intermediate.**

Create a backend.yml file (not provided here) to be run by docker-compose. Create an intermediate service on the file.

```yaml
services:
  intermediate:
    image: "worsica/worsica-intermediate:development"
    container_name: "intermediate"
    hostname: "intermediate"
    volumes:
      - /host/path/worsica-intermediate:/usr/local/worsica_web_intermediate
      - /host/path/worsica-processing:/usr/local/worsica_web_products
      - /host/path/temp/netcdf/bin/FES2014/data:/usr/local/bin/FES2014/data
      - /dev/log:/dev/log
      - /etc/hosts:/etc/hosts
      - /host/path/certs:/usr/local/worsica_web_intermediate/certs
    entrypoint: "/bin/bash"
    command: "/usr/local/worsica_web_intermediate/worsica_runserver.sh"
    depends_on:
      - postgis
      - rabbitmq
      - nextcloud
    networks:
      - worsica
    ports:
      - "8002:8002"
```

On volumes, remember to replace the /host/path/ by the actual directories you have at host.

You need to create the postgis, rabbitmq and nextcloud services too in order to run this. Do a search for a yml config for these services.

Then run docker-compose:

```shell
docker-compose -f backend/backend.yml up -d intermediate

```

If everything goes well, you can enter on the container:

```shell
docker exec -it intermediate bash
```

If you want to restart the container:

```shell
docker restart intermediate
```
