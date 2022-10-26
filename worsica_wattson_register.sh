export WATTSON_ISSUER=egi
export WATTSON_TOKEN=`oidc-token egi`
export WATTSON_URL=https://watts-prod.data.kit.edu
wattson request VOMS_CERT_Worsica > /tmp/x509up_u0
