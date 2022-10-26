CUSTOM_HOME="/usr/local"
if (echo -e "NEXTCLOUD_USER = '${WORSICA_NEXTCLOUD_USER}'\n
NEXTCLOUD_PWD = '${WORSICA_NEXTCLOUD_PASSWORD}'\n
NEXTCLOUD_DOMAIN = 'nextcloud'\n
NEXTCLOUD_URL_PATH = 'http://'+NEXTCLOUD_DOMAIN+'/remote.php/dav/files/'+NEXTCLOUD_USER\n" > $CUSTOM_HOME/worsica_web_intermediate/worsica_web_intermediate/nextcloud_access.py) ; then
	echo '[OK] Successfully created the nextcloud access'
else
	echo '[Error] Something went wrong on creating the nextcloud access. Aborting!'
	exit 1
fi
