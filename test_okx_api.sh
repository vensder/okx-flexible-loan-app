#!/bin/bash
# OKX API Authentication Test using curl

# Load credentials from environment
API_KEY="${OKX_API_KEY}"
SECRET_KEY="${OKX_SECRET_KEY}"
PASSPHRASE="${OKX_PASSPHRASE}"

if [ -z "$API_KEY" ] || [ -z "$SECRET_KEY" ] || [ -z "$PASSPHRASE" ]; then
    echo "❌ Error: Missing credentials"
    echo "Please set: OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE"
    exit 1
fi

echo "🔍 Testing OKX API Authentication"
echo "=================================="
echo ""

# Endpoint to test
METHOD="GET"
REQUEST_PATH="/api/v5/account/balance"
BODY=""

# Generate timestamp (ISO8601 format)
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ")

echo "📋 Request Details:"
echo "  Method: $METHOD"
echo "  Path: $REQUEST_PATH"
echo "  Timestamp: $TIMESTAMP"
echo "  API Key (first 8): ${API_KEY:0:8}..."
echo "  Passphrase: $PASSPHRASE"
echo ""

# Create signature
# Format: timestamp + method + requestPath + body
SIGN_STRING="${TIMESTAMP}${METHOD}${REQUEST_PATH}${BODY}"

echo "🔐 String to sign:"
echo "  $SIGN_STRING"
echo ""

# Generate HMAC SHA256 signature and base64 encode
SIGNATURE=$(echo -n "$SIGN_STRING" | openssl dgst -sha256 -hmac "$SECRET_KEY" -binary | base64)

echo "✍️  Generated signature (first 20 chars):"
echo "  ${SIGNATURE:0:20}..."
echo "${SIGNATURE}"


exit 0

# Make the request
echo "🚀 Making API request..."
echo ""

RESPONSE=$(curl -s -w "\nHTTP_CODE:%{http_code}" \
  -X GET \
  -H "OK-ACCESS-KEY: $API_KEY" \
  -H "OK-ACCESS-SIGN: $SIGNATURE" \
  -H "OK-ACCESS-TIMESTAMP: $TIMESTAMP" \
  -H "OK-ACCESS-PASSPHRASE: $PASSPHRASE" \
  -H "Content-Type: application/json" \
  "https://www.okx.com${REQUEST_PATH}")

# Extract HTTP code and body
HTTP_CODE=$(echo "$RESPONSE" | grep "HTTP_CODE:" | cut -d: -f2)
BODY_RESPONSE=$(echo "$RESPONSE" | sed '/HTTP_CODE:/d')

echo "📥 Response:"
echo "  HTTP Status: $HTTP_CODE"
echo ""
echo "$BODY_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$BODY_RESPONSE"
echo ""

# Interpret results
if [ "$HTTP_CODE" = "200" ]; then
    CODE=$(echo "$BODY_RESPONSE" | grep -o '"code":"[^"]*"' | cut -d'"' -f4)
    if [ "$CODE" = "0" ]; then
        echo "✅ SUCCESS! Authentication is working correctly."
    else
        echo "⚠️  API returned error code: $CODE"
        echo "Check the response message above for details."
    fi
elif [ "$HTTP_CODE" = "403" ]; then
    echo "❌ 403 Forbidden - Authentication failed"
    echo ""
    echo "Common causes:"
    echo "  1. Invalid API key or secret"
    echo "  2. Incorrect passphrase (case-sensitive!)"
    echo "  3. IP whitelist restriction on your API key"
    echo "  4. API key doesn't have 'Read' permission for Account"
    echo "  5. Signature generation issue"
else
    echo "❌ Unexpected HTTP status: $HTTP_CODE"
fi
