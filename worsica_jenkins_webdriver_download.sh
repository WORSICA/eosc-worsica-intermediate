HOME_PATH=$(pwd)
echo $HOME_PATH
cd $HOME_PATH
cd functional_tests_files

#chrome
echo "Get chrome driver"
wget -O chromedriver-version.txt https://chromedriver.storage.googleapis.com/LATEST_RELEASE_$(IFS='.' read major minor build patch < <(google-chrome --product-version); echo "${major}.${minor}.${build}")
echo $(cat chromedriver-version.txt )
wget -O chromedriver_linux64.zip https://chromedriver.storage.googleapis.com/$(cat chromedriver-version.txt )/chromedriver_linux64.zip
unzip -o chromedriver_linux64.zip -d ./chromedriver_linux64
cd /var/tmp
curl -O https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm
yum install -y google-chrome-stable_current_x86_64.rpm
rm -rf google-chrome-stable_current_x86_64.rpm

#ff
cd $HOME_PATH
cd functional_tests_files
echo "Get ff driver"
sudo yum install jq -y
json=$(curl -s https://api.github.com/repos/mozilla/geckodriver/releases/latest)
url=$(echo "$json" | jq -r '.assets[].browser_download_url | select(contains("linux64")) | select(endswith("tar.gz"))')
wget -O geckodriver_linux64.tar.gz "$url"
mkdir geckodriver_linux64
echo $(pwd)
tar -xzf geckodriver_linux64.tar.gz -C ./geckodriver_linux64
yum install -y firefox

echo "Done!"
cd ..
