#!/bin/bash -e

export DJANGO_SETTINGS_MODULE=shipyard.settings
rm -rf CodeResources
rm -rf VerificationScripts
rm -rf Datasets
rm -rf Logs
rm -rf VerificationLogs

./nukeDB.expect

python2.7 manage.py loaddata initial_data
python2.7 manage.py loaddata initial_user
python2.7 load_default_objects.py > /dev/null
