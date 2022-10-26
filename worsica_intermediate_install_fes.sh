TEMP_DIR=/home/centos/temp/netcdf
if ([ ! -d "$TEMP_DIR" ]) ; then
	echo "$TEMP_DIR not found, make temporary dir "
	mkdir $TEMP_DIR
else
	echo "$TEMP_DIR exists "
fi
echo "goto temporary dir to $TEMP_DIR"
cd $TEMP_DIR

if ([ ! -d "$TEMP_DIR/bin/FES2014/data/" ]) ; then
	echo "$TEMP_DIR/bin/FES2014/data/ does not exist, check if file zip exists "
	if ([ ! -f "$TEMP_DIR/fes_netcdf.zip" ]) ; then
		echo "$TEMP_DIR/fes_netcdf.zip not found, download it "
		if curl -L https://www.dropbox.com/sh/5m5somj043m6qp6/AAC3C0D8rXe2u05bDdePgtCta/data?dl=1 -o fes_netcdf.zip ; then
			echo "downloaded!"
		else
			echo "failed!"
			exit 1
		fi
	else
		echo "$TEMP_DIR/fes_netcdf.zip exists"
	fi
	
	echo "extract to $TEMP_DIR/bin/FES2014/data/ "
	if 7z x -o$TEMP_DIR/bin/FES2014/data/ $TEMP_DIR/fes_netcdf.zip ; then
		echo "extracted, now remove zip file"
		rm -rf $TEMP_DIR/fes_netcdf.zip
		echo "removed"
	else
		echo "failed!"
		exit 1
	fi	
else
	echo "$TEMP_DIR/bin/FES2014/data/ exists"
fi
cd $HOME
