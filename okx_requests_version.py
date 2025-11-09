#!/usr/bin/env python3
"""
OKX Loan Monitor using requests library (more reliable)
Install: pip install requests
"""

import os
import json
import hmac
import base64
import hashlib
from datetime import datetime, timezone
from typing import Dict, Optional

try:
    import requests
except ImportError:
    print("❌ Error: 'requests' library not installed")
    print("Install it with: pip install requests")
    exit(1)


class OKXLoanMonitor:
    def __init__(self, api_key: str, secret_key: str, passphrase: str, flag: str = "0", debug: bool = False):
        self.api_key = api_key.strip()
        self.secret_key = secret_key.strip()
        self.passphrase = passphrase.strip()
        self.base_url = "https://www.okx.com"
        self.debug = debug

    def _get_timestamp(self) -> str:
        """Get ISO8601 timestamp"""
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = '') -> str:
        """Create signature for API request"""
        message = timestamp + method + request_path + body
        mac = hmac.new(
            self.secret_key.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode('utf-8')

    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """Make authenticated request using requests library"""
        timestamp = self._get_timestamp()
        request_path = endpoint

        if params and method == 'GET':
            query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
            request_path = f"{endpoint}?{query_string}"

        body = ''
        if params and method == 'POST':
            body = json.dumps(params)

        sign = self._sign(timestamp, method, request_path, body)

        headers = {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': sign,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }

        url = self.base_url + request_path

        if self.debug:
            print(f"\n🔍 REQUEST:")
            print(f"  URL: {url}")
            print(f"  Method: {method}")
            print(f"  Timestamp: {timestamp}")
            print(
                f"  String to sign: {timestamp + method + request_path + body}")
            print(f"  Signature: {sign[:20]}...")

        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=10)
            else:
                response = requests.post(
                    url, headers=headers, data=body, timeout=10)

            if self.debug:
                print(f"  Status Code: {response.status_code}")

            return response.json()

        except requests.exceptions.RequestException as e:
            return {"code": "error", "msg": str(e)}

    def get_account_balance(self) -> Dict:
        return self._request('GET', '/api/v5/account/balance')

    def get_flexible_loan_info(self) -> Dict:
        return self._request('GET', '/api/v5/finance/flexible-loan/loan-info')

    def calculate_metrics(self, balance_data: Dict) -> Dict:
        metrics = {
            'total_equity': 0.0,
            'total_debt': 0.0,
            'current_ltv': 0.0,
            'currencies': []
        }

        if balance_data.get('code') != '0':
            return metrics

        data = balance_data.get('data', [])
        if not data:
            return metrics

        details = data[0].get('details', [])

        for detail in details:
            ccy = detail.get('ccy', '')

            # Safely convert to float, handling empty strings
            eq_val = detail.get('eq', '0')
            eq = float(eq_val) if eq_val and eq_val != '' else 0.0

            liab_val = detail.get('liab', '0')
            liab = float(liab_val) if liab_val and liab_val != '' else 0.0

            avail_val = detail.get('availEq', '0')
            avail = float(avail_val) if avail_val and avail_val != '' else 0.0

            if eq > 0 or liab > 0:
                metrics['currencies'].append({
                    'currency': ccy,
                    'equity': eq,
                    'debt': liab,
                    'available': avail
                })

            metrics['total_debt'] += liab

        total_eq_usd = float(data[0].get('totalEq', '0') or 0)
        adj_eq = float(data[0].get('adjEq', '0') or 0)

        if metrics['total_debt'] > 0 and adj_eq > 0:
            metrics['current_ltv'] = (metrics['total_debt'] / adj_eq) * 100

        metrics['total_equity_usd'] = total_eq_usd
        metrics['adjusted_equity'] = adj_eq
        metrics['margin_ratio'] = data[0].get('mgnRatio', '')

        return metrics

    def display_metrics(self, metrics: Dict):
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
        print("Fetching loan data from OKX...")

        balance = self.get_account_balance()

        loan_info = self.get_flexible_loan_info()

        if self.debug:
            print("\n🔍 RAW ACCOUNT BALANCE INFO:")
            print(json.dumps(balance, indent=2))
            print("\n RAW FLEXIBLE LOAN INFO:")
            print(json.dumps(loan_info, indent=2))

        if balance.get('code') == '0':
            metrics = self.calculate_metrics(balance)
            self.display_metrics(metrics)
        else:
            print(f"❌ Error: {balance.get('msg', 'Unknown error')}")
            print(f"Code: {balance.get('code', 'N/A')}")


def main():
    import sys

    debug = '--debug' in sys.argv or '-d' in sys.argv

    api_key = os.getenv('OKX_API_KEY')
    secret_key = os.getenv('OKX_SECRET_KEY')
    passphrase = os.getenv('OKX_PASSPHRASE')
    flag = os.getenv('OKX_FLAG', '0')

    if not all([api_key, secret_key, passphrase]):
        print("❌ ERROR: Missing API credentials!")
        print("\nPlease set environment variables:")
        print("  export OKX_API_KEY='...'")
        print("  export OKX_SECRET_KEY='...'")
        print("  export OKX_PASSPHRASE='...'")
        return

    monitor = OKXLoanMonitor(api_key, secret_key, passphrase, flag, debug)
    monitor.run()


if __name__ == "__main__":
    main()
