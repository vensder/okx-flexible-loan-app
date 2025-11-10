# Use the same bash test script approach
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ")
METHOD="GET"
REQUEST_PATH="/api/v5/finance/flexible-loan/collateral-assets"
SIGN_STRING="${TIMESTAMP}${METHOD}${REQUEST_PATH}"
SIGNATURE=$(echo -n "$SIGN_STRING" | openssl dgst -sha256 -hmac "$OKX_SECRET_KEY" -binary | base64)

curl -X GET \
  -H "OK-ACCESS-KEY: $OKX_API_KEY" \
  -H "OK-ACCESS-SIGN: $SIGNATURE" \
  -H "OK-ACCESS-TIMESTAMP: $TIMESTAMP" \
  -H "OK-ACCESS-PASSPHRASE: $OKX_PASSPHRASE" \
  -H "Content-Type: application/json" \
  "https://www.okx.com/api/v5/finance/flexible-loan/collateral-assets" | python3 -m json.tool
