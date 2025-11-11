#!/usr/bin/env python3
"""
OKX Flexible Multicollateral Loan Monitor
Command-line utility using direct HTTP requests (minimal dependencies)
"""

import os
import json
import hmac
import base64
import hashlib
from datetime import datetime
from typing import Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


class OKXLoanMonitor:
    def __init__(self, api_key: str, secret_key: str, passphrase: str, flag: str = "0", debug: bool = False):
        """
        Initialize OKX API client
        flag: "0" for production, "1" for demo trading
        """
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.base_url = "https://www.okx.com" if flag == "0" else "https://www.okx.com"
        self.debug = debug

    def _get_timestamp(self) -> str:
        """Get ISO8601 timestamp"""
        from datetime import timezone
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = '') -> str:
        """Create signature for API request"""
        message = timestamp + method + request_path + body

        if self.debug:
            print(f"\n🔐 SIGNATURE DEBUG:")
            print(f"Message to sign: '{message}'")
            print(f"Message length: {len(message)}")

        mac = hmac.new(
            bytes(self.secret_key, encoding='utf-8'),
            bytes(message, encoding='utf-8'),
            digestmod=hashlib.sha256
        )
        signature = base64.b64encode(mac.digest()).decode()

        if self.debug:
            print(f"Generated signature: {signature[:20]}...")

        return signature

    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """Make authenticated request to OKX API"""
        timestamp = self._get_timestamp()
        request_path = endpoint

        # Add query parameters if present
        if params and method == 'GET':
            query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
            request_path = f"{endpoint}?{query_string}"

        body = ''
        if params and method == 'POST':
            body = json.dumps(params)

        sign = self._sign(timestamp, method, request_path, body)

        # Strip any whitespace from credentials
        api_key = self.api_key.strip()
        passphrase = self.passphrase.strip()

        headers = {
            'OK-ACCESS-KEY': api_key,
            'OK-ACCESS-SIGN': sign,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': passphrase,
            'Content-Type': 'application/json'
        }

        url = self.base_url + request_path

        if self.debug:
            print(f"\n🔍 REQUEST DETAILS:")
            print(f"Method: {method}")
            print(f"URL: {url}")
            print(f"Timestamp: {timestamp}")
            print(f"Request Path: {request_path}")
            print(f"Body: {body if body else '(empty)'}")
            print(f"API Key (first 8): {api_key[:8]}...")
            print(f"API Key length: {len(api_key)}")
            print(f"Signature (first 16): {sign[:16]}...")
            print(f"Passphrase: '{passphrase}'")
            print(f"Passphrase length: {len(passphrase)}")

            # Show what we're signing
            sign_string = timestamp + method + request_path + body
            print(f"\nString to sign: {sign_string}")
            print(f"String length: {len(sign_string)}")

            print(f"\nHeaders being sent:")
            for key, value in headers.items():
                if key == 'OK-ACCESS-SIGN':
                    print(f"  {key}: {value[:20]}...")
                elif key == 'OK-ACCESS-KEY':
                    print(f"  {key}: {value[:16]}...")
                else:
                    print(f"  {key}: {value}")

        try:
            req = Request(url, headers=headers, method=method)
            if body:
                req.data = body.encode('utf-8')

            with urlopen(req) as response:
                result = json.loads(response.read().decode('utf-8'))
                if self.debug:
                    print(f"\n✅ SUCCESS - Response code: {result.get('code')}")
                return result
        except HTTPError as e:
            error_body = e.read().decode('utf-8')
            if self.debug:
                print(f"\n❌ HTTP ERROR:")
                print(f"Status Code: {e.code}")
                print(f"Error Body: {error_body}")
                print(f"Error Headers: {e.headers}")
            return {"code": str(e.code), "msg": error_body}
        except URLError as e:
            if self.debug:
                print(f"\n❌ URL ERROR: {e.reason}")
            return {"code": "error", "msg": str(e.reason)}
        except Exception as e:
            if self.debug:
                print(f"\n❌ EXCEPTION: {type(e).__name__}: {e}")
            return {"code": "error", "msg": str(e)}

    def get_account_balance(self) -> Dict:
        """Get account balance including loan information"""
        return self._request('GET', '/api/v5/account/balance')

    def get_account_config(self) -> Dict:
        """Get account configuration including margin mode"""
        return self._request('GET', '/api/v5/account/config')

    def get_max_loan(self, inst_id: str, mg_mode: str = 'cross') -> Dict:
        """Get maximum borrowable amount"""
        params = {'instId': inst_id, 'mgnMode': mg_mode}
        return self._request('GET', '/api/v5/account/max-loan', params)

    def calculate_metrics(self, balance_data: Dict) -> Dict:
        """Calculate loan metrics from balance data"""
        metrics = {
            'total_equity': 0.0,
            'total_debt': 0.0,
            'total_collateral': 0.0,
            'current_ltv': 0.0,
            'currencies': []
        }

        if balance_data.get('code') != '0':
            return metrics

        data = balance_data.get('data', [])
        if not data:
            return metrics

        # Multi-currency margin account details
        details = data[0].get('details', [])

        for detail in details:
            ccy = detail.get('ccy', '')
            eq = float(detail.get('eq', 0))  # Equity
            liab = float(detail.get('liab', 0))  # Liabilities (borrowed)
            cash_bal = float(detail.get('cashBal', 0))  # Cash balance

            if eq > 0 or liab > 0:
                metrics['currencies'].append({
                    'currency': ccy,
                    'equity': eq,
                    'debt': liab,
                    'cash_balance': cash_bal,
                    'available': float(detail.get('availEq', 0))
                })

            metrics['total_debt'] += liab

        # Total equity in USD
        total_eq_usd = float(data[0].get('totalEq', 0))

        # Adjusted equity (collateral value after haircuts)
        adj_eq = float(data[0].get('adjEq', 0))

        # Isolated margin equity
        iso_eq = float(data[0].get('isoEq', 0))

        # Maintenance margin requirement
        mmr = float(data[0].get('mmr', 0))

        # Margin ratio
        mgn_ratio = data[0].get('mgnRatio', '')

        # Calculate LTV if there's debt
        if metrics['total_debt'] > 0 and adj_eq > 0:
            metrics['current_ltv'] = (metrics['total_debt'] / adj_eq) * 100

        metrics['total_equity_usd'] = total_eq_usd
        metrics['adjusted_equity'] = adj_eq
        metrics['isolated_equity'] = iso_eq
        metrics['maintenance_margin'] = mmr
        metrics['margin_ratio'] = mgn_ratio

        return metrics

    def display_metrics(self, metrics: Dict):
        """Display loan metrics in a formatted way"""
        print("\n" + "="*60)
        print("OKX FLEXIBLE MULTICOLLATERAL LOAN OVERVIEW")
        print("="*60)
        print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("-"*60)

        print(f"\n📊 OVERALL METRICS")
        print(
            f"  Total Equity (USD):        ${metrics.get('total_equity_usd', 0):,.2f}")
        print(
            f"  Adjusted Equity (USD):     ${metrics.get('adjusted_equity', 0):,.2f}")
        print(
            f"  Total Debt (USD):          ${metrics.get('total_debt', 0):,.2f}")

        if metrics.get('margin_ratio'):
            print(f"  Margin Ratio:              {metrics['margin_ratio']}")

        if metrics.get('current_ltv', 0) > 0:
            ltv = metrics['current_ltv']
            print(f"  Current LTV:               {ltv:.2f}%")

            # Color-coded risk indication
            if ltv < 50:
                risk = "✅ LOW"
            elif ltv < 70:
                risk = "⚠️  MEDIUM"
            else:
                risk = "🚨 HIGH"
            print(f"  Risk Level:                {risk}")
        else:
            print(f"  Current LTV:               0.00% (No debt)")

        print(f"\n💰 CURRENCY BREAKDOWN")
        currencies = metrics.get('currencies', [])
        if currencies:
            print(
                f"  {'Currency':<10} {'Equity':<15} {'Debt':<15} {'Available':<15}")
            print(f"  {'-'*10} {'-'*15} {'-'*15} {'-'*15}")
            for curr in currencies:
                print(
                    f"  {curr['currency']:<10} {curr['equity']:<15.8f} {curr['debt']:<15.8f} {curr['available']:<15.8f}")
        else:
            print("  No active currencies")

        print("\n" + "="*60 + "\n")

    def run(self):
        """Main execution method"""
        print("Fetching loan data from OKX...")

        if self.debug:
            print("\n🔍 DEBUG MODE ENABLED")
            print(f"API Key (first 8 chars): {self.api_key[:8]}...")
            print(f"Base URL: {self.base_url}")
            print(f"Passphrase set: {'Yes' if self.passphrase else 'No'}")

        # Get account balance
        balance = self.get_account_balance()

        if self.debug:
            print("\n🔍 RAW API RESPONSE:")
            print(json.dumps(balance, indent=2))

        if balance.get('code') == '0':
            metrics = self.calculate_metrics(balance)
            self.display_metrics(metrics)
        else:
            print(f"❌ Error: {balance.get('msg', 'Unknown error')}")
            print(f"Code: {balance.get('code', 'N/A')}")

            if self.debug:
                print("\n🔍 TROUBLESHOOTING TIPS:")
                print("1. Verify API key has 'Read' permission for Account")
                print("2. Check that API key is not restricted by IP whitelist")
                print("3. Ensure passphrase is correct (case-sensitive)")
                print("4. Try regenerating the API key if issues persist")
            else:
                print(
                    "\n💡 Run with --debug flag for more details: ./okx_loan_monitor.py --debug")


def main():
    """Main entry point"""
    import sys

    # Check for debug flag
    debug = '--debug' in sys.argv or '-d' in sys.argv

    # Load credentials from environment variables
    api_key = os.getenv('OKX_API_KEY')
    secret_key = os.getenv('OKX_SECRET_KEY')
    passphrase = os.getenv('OKX_PASSPHRASE')
    flag = os.getenv('OKX_FLAG', '0')  # 0 for production, 1 for demo

    if not all([api_key, secret_key, passphrase]):
        print("❌ ERROR: Missing API credentials!")
        print("\nPlease set the following environment variables:")
        print("  export OKX-API-KEY='your-okx-api-key'".replace("-", "_"))
        print("  export OKX-SECRET-KEY='your-okx-secret-key'".replace("-", "_"))
        print("  export OKX-PASSPHRASE='your-passphrase'".replace("-", "_"))
        print("  export OKX_FLAG='0'  # 0 for production, 1 for demo")
        return

    monitor = OKXLoanMonitor(api_key, secret_key, passphrase, flag, debug)
    monitor.run()


if __name__ == "__main__":
    main()
