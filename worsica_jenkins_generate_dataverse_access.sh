CUSTOM_HOME="/usr/local"
if (echo -e "DATAVERSE_DOMAIN = 'dataverse'\n
DATAVERSE_BASE_URL = 'http://'+DATAVERSE_DOMAIN+':8080'\n
DATAVERSE_ALIAS = '${WORSICA_DATAVERSE_USER}'\n
DATAVERSE_API_TOKEN = '${WORSICA_DATAVERSE_PASSWORD}'\n" > $CUSTOM_HOME/worsica_web_intermediate/worsica_web_intermediate/dataverse_access.py) ; then
	echo '[OK] Successfully created the dataverse access'
else
	echo '[Error] Something went wrong on creating the dataverse access. Aborting!'
	exit 1
fi
