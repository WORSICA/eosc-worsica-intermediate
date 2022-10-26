#!/bin/sh -ex
#(Omitted Copy certs for arcce)

echo "(Re)Start celery"
pkill -9 -f 'celery worker' || true
rm -rf ./w1.pid
rm -rf ./w1.log
celery multi start w1 -A worsica_web_intermediate -B -l info --concurrency=24 --max-tasks-per-child=1
(for f in w1-*; do > $f; done)
echo "Mark running simulations as error (stop them)"
python3 ./worsica_intermediate_stop_all_processings.py
echo "(Re)Start Django"
pkill -9 -f 'python3 ./manage.py runserver' || true
python3 ./manage.py runserver 0.0.0.0:8002
tail -f /dev/null
