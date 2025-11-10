#!/usr/bin/env python3
"""
OKX Flexible Multicollateral Loan Monitor
"""

import os
import json
import hmac
import base64
import hashlib
import requests
from datetime import datetime, timezone
from typing import Dict, Optional


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

        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=10)
            else:
                response = requests.post(
                    url, headers=headers, data=body, timeout=10)

            return response.json()

        except requests.exceptions.RequestException as e:
            return {"code": "error", "msg": str(e)}

    def get_account_balance(self) -> Dict:
        """Get trading account balance"""
        return self._request('GET', '/api/v5/account/balance')

    def get_flexible_loan_info(self) -> Dict:
        """Get flexible loan information"""
        return self._request('GET', '/api/v5/finance/flexible-loan/loan-info')

    def get_collateral_assets(self) -> Dict:
        """Get detailed collateral assets with USD values - NOT USED, returns funding account only"""
        return self._request('GET', '/api/v5/finance/flexible-loan/collateral-assets')

    def get_tickers(self, inst_type: str = "SPOT") -> Dict:
        """Get all tickers at once for price calculation"""
        return self._request('GET', '/api/v5/market/tickers', {'instType': inst_type})

    def parse_loan_info(self, loan_data: Dict, tickers_data: Dict = None) -> Dict:
        """Parse flexible loan information with USD values calculated from tickers"""
        loan_metrics = {
            'has_loan': False,
            'collateral_usd': 0.0,
            'loan_usd': 0.0,
            'current_ltv': 0.0,
            'margin_call_ltv': 0.0,
            'liquidation_ltv': 0.0,
            'collateral_assets': [],
            'loan_assets': []
        }

        if loan_data.get('code') != '0':
            return loan_metrics

        data = loan_data.get('data', [])
        if not data:
            return loan_metrics

        loan_info = data[0]
        loan_metrics['has_loan'] = True

        # Build price map from tickers
        prices = {}
        if tickers_data and tickers_data.get('code') == '0':
            tickers = tickers_data.get('data', [])
            for ticker in tickers:
                inst_id = ticker.get('instId', '')
                # Parse pairs like BTC-USDT, ETH-USDC, etc.
                if '-' in inst_id:
                    base, quote = inst_id.split('-', 1)
                    if quote in ['USDT', 'USDC', 'USD']:
                        last_price = float(ticker.get('last', 0))
                        if last_price > 0:
                            # Store the first valid price found for each currency
                            if base not in prices:
                                prices[base] = last_price

        # Stablecoins are always $1
        for stable in ['USDT', 'USDC', 'USD', 'DAI', 'TUSD', 'BUSD']:
            prices[stable] = 1.0

        # Parse collateral with calculated USD values
        collateral_list = loan_info.get('collateralData', [])
        for item in collateral_list:
            amt = float(item.get('amt', 0))
            ccy = item.get('ccy', '')
            if amt > 0:
                price = prices.get(ccy, 0.0)
                usd_value = amt * price if price > 0 else 0.0

                loan_metrics['collateral_assets'].append({
                    'currency': ccy,
                    'amount': amt,
                    'usd_value': usd_value,
                    'price': price
                })

        # Parse loans
        loan_data_list = loan_info.get('loanData', [])
        for item in loan_data_list:
            amt = float(item.get('amt', 0))
            if amt > 0:
                loan_metrics['loan_assets'].append({
                    'currency': item.get('ccy', ''),
                    'amount': amt
                })

        # Parse metrics from OKX (already calculated correctly)
        loan_metrics['collateral_usd'] = float(
            loan_info.get('collateralNotionalUsd', 0))
        loan_metrics['loan_usd'] = float(loan_info.get('loanNotionalUsd', 0))
        loan_metrics['current_ltv'] = float(loan_info.get('curLTV', 0)) * 100
        loan_metrics['margin_call_ltv'] = float(
            loan_info.get('marginCallLTV', 0)) * 100
        loan_metrics['liquidation_ltv'] = float(
            loan_info.get('liqLTV', 0)) * 100

        return loan_metrics

    def calculate_account_metrics(self, balance_data: Dict) -> Dict:
        """Calculate metrics from trading account balance"""
        metrics = {
            'total_equity_usd': 0.0,
            'currencies': []
        }

        if balance_data.get('code') != '0':
            return metrics

        data = balance_data.get('data', [])
        if not data:
            return metrics

        total_eq_val = data[0].get('totalEq', '0')
        metrics['total_equity_usd'] = float(
            total_eq_val) if total_eq_val and total_eq_val != '' else 0.0

        details = data[0].get('details', [])

        for detail in details:
            ccy = detail.get('ccy', '')

            eq_val = detail.get('eq', '0')
            eq = float(eq_val) if eq_val and eq_val != '' else 0.0

            avail_val = detail.get('availEq', '0')
            avail = float(avail_val) if avail_val and avail_val != '' else 0.0

            if eq > 0:
                metrics['currencies'].append({
                    'currency': ccy,
                    'equity': eq,
                    'available': avail
                })

        return metrics

    def display_combined_metrics(self, account_metrics: Dict, loan_metrics: Dict):
        """Display comprehensive loan and account information"""
        print("\n" + "="*70)
        print("OKX FLEXIBLE MULTICOLLATERAL LOAN OVERVIEW")
        print("="*70)
        print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("-"*70)

        if not loan_metrics.get('has_loan'):
            print("\n⚠️  No active flexible loan found")
            print(
                f"\n💰 Trading Account Balance: ${account_metrics.get('total_equity_usd', 0):,.2f}")
            return

        # Main loan metrics
        print(f"\n📊 LOAN SUMMARY")
        print(
            f"  Collateral Value (USD):    ${loan_metrics['collateral_usd']:,.2f}")
        print(f"  Loan Amount (USD):         ${loan_metrics['loan_usd']:,.2f}")
        print(
            f"  Current LTV:               {loan_metrics['current_ltv']:.2f}%")
        print(
            f"  Margin Call LTV:           {loan_metrics['margin_call_ltv']:.2f}%")
        print(
            f"  Liquidation LTV:           {loan_metrics['liquidation_ltv']:.2f}%")

        # Risk assessment
        current_ltv = loan_metrics['current_ltv']
        margin_call_ltv = loan_metrics['margin_call_ltv']
        liq_ltv = loan_metrics['liquidation_ltv']

        ltv_to_margin_call = margin_call_ltv - current_ltv
        ltv_to_liquidation = liq_ltv - current_ltv
        margin_call_pct = (current_ltv / margin_call_ltv) * 100

        print(f"\n⚠️  RISK STATUS")

        if margin_call_pct < 70:
            risk = "✅ SAFE"
        elif margin_call_pct < 85:
            risk = "⚠️  CAUTION"
        elif margin_call_pct < 95:
            risk = "🟠 WARNING"
        elif current_ltv < margin_call_ltv:
            risk = "🔴 HIGH RISK"
        else:
            risk = "🚨 MARGIN CALL ACTIVE"

        print(f"  Risk Level:                {risk}")
        print(f"  LTV vs Margin Call:        {margin_call_pct:.1f}%")
        print(f"  Buffer to Margin Call:     {ltv_to_margin_call:.2f}% LTV")
        print(f"  Buffer to Liquidation:     {ltv_to_liquidation:.2f}% LTV")

        if ltv_to_margin_call > 0:
            collateral_drop_pct = (ltv_to_margin_call / margin_call_ltv) * 100
            print(
                f"  Collateral can drop:       {collateral_drop_pct:.1f}% before margin call")

        # Loan breakdown
        print(f"\n💸 BORROWED")
        if loan_metrics['loan_assets']:
            for asset in loan_metrics['loan_assets']:
                print(f"  {asset['currency']:<10} {asset['amount']:>20,.8f}")
            print(f"  {'TOTAL (USD)':<10} ${loan_metrics['loan_usd']:>19,.2f}")

        # Collateral breakdown
        print(
            f"\n🔒 COLLATERAL ({len(loan_metrics['collateral_assets'])} assets)")
        if loan_metrics['collateral_assets']:
            # Sort by USD value (descending)
            sorted_collateral = sorted(
                loan_metrics['collateral_assets'],
                key=lambda x: x.get('usd_value', 0),
                reverse=True
            )

            print(f"  {'Currency':<10} {'Amount':<20} {'USD Value':>15}")
            print(f"  {'-'*10} {'-'*20} {'-'*15}")

            total_shown_usd = 0.0
            shown_count = 0

            for asset in sorted_collateral:
                usd_val = asset.get('usd_value', 0)

                # Show assets with USD value > $1
                if shown_count < 20 and usd_val > 1.0:
                    price = asset.get('price', 0)
                    print(
                        f"  {asset['currency']:<10} {asset['amount']:>20,.8f} ${usd_val:>14,.2f}")
                    total_shown_usd += usd_val
                    shown_count += 1

            # Count remaining dust
            dust_count = len(sorted_collateral) - shown_count
            dust_value = loan_metrics['collateral_usd'] - total_shown_usd

            if dust_count > 0:
                print(
                    f"  ... and {dust_count} more (${dust_value:,.2f} in dust)")

            print(f"  {'-'*47}")
            print(
                f"  {'TOTAL (USD)':<10} {'':<20} ${loan_metrics['collateral_usd']:>14,.2f}")

        # Trading account
        print(f"\n💰 TRADING ACCOUNT (Available for Operations)")
        print(
            f"  Total Balance:             ${account_metrics.get('total_equity_usd', 0):,.2f}")

        if account_metrics.get('currencies'):
            significant_assets = [
                c for c in account_metrics['currencies']
                if c['equity'] > 0.0001
            ]

            if significant_assets:
                print(f"\n  Top Assets:")
                print(f"  {'Currency':<10} {'Balance':<15} {'Available':<15}")
                print(f"  {'-'*10} {'-'*15} {'-'*15}")

                for curr in significant_assets[:10]:
                    print(
                        f"  {curr['currency']:<10} {curr['equity']:>15,.8f} {curr['available']:>15,.8f}")

                if len(significant_assets) > 10:
                    print(
                        f"  ... and {len(significant_assets) - 10} more currencies")

        print("\n" + "="*70 + "\n")

    def run(self):
        """Main execution method"""
        print("Fetching data from OKX...")

        balance = self.get_account_balance()
        loan_info = self.get_flexible_loan_info()
        tickers = self.get_tickers()  # Get all spot tickers at once

        if self.debug:
            print(f"\n🔍 DEBUG - Got {len(tickers.get('data', []))} tickers")
            print("\n🔍 DEBUG - Loan Info Collateral Data (first 5):")
            if loan_info.get('code') == '0' and loan_info.get('data'):
                collateral = loan_info['data'][0].get('collateralData', [])
                for i, item in enumerate(collateral[:5]):
                    print(f"  {i+1}. {item.get('ccy')}: {item.get('amt')}")
                print(f"  ... total {len(collateral)} collateral assets")

        account_metrics = self.calculate_account_metrics(balance)
        loan_metrics = self.parse_loan_info(loan_info, tickers)

        self.display_combined_metrics(account_metrics, loan_metrics)


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
