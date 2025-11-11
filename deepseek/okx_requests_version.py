#!/usr/bin/env python3
"""
OKX Flexible Multicollateral Loan Monitor
Optimized version with SQLite caching between runs
"""

import os
import json
import hmac
import base64
import hashlib
import requests
import time
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Optional, List
from decimal import Decimal
import atexit


class PriceCache:
    def __init__(self, db_path: str = "okx_cache.db"):
        self.db_path = db_path
        self._init_db()
        atexit.register(self.cleanup_old_entries)

    def _init_db(self):
        """Initialize SQLite database"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS price_cache (
                    currency TEXT PRIMARY KEY,
                    price REAL NOT NULL,
                    timestamp INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                )
            ''')
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_expires_at ON price_cache(expires_at)')
            conn.commit()

    def get(self, currency: str) -> Optional[float]:
        """Get price from cache if not expired"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                'SELECT price FROM price_cache WHERE currency = ? AND expires_at > ?',
                (currency, int(time.time()))
            )
            result = cursor.fetchone()
            return result[0] if result else None

    def set(self, currency: str, price: float, ttl: int = 300):
        """Set price in cache with TTL (default 5 minutes)"""
        timestamp = int(time.time())
        expires_at = timestamp + ttl

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                '''INSERT OR REPLACE INTO price_cache
                   (currency, price, timestamp, expires_at)
                   VALUES (?, ?, ?, ?)''',
                (currency, price, timestamp, expires_at)
            )
            conn.commit()

    def set_batch(self, prices: Dict[str, float], ttl: int = 300):
        """Set multiple prices at once"""
        timestamp = int(time.time())
        expires_at = timestamp + ttl

        with sqlite3.connect(self.db_path) as conn:
            for currency, price in prices.items():
                conn.execute(
                    '''INSERT OR REPLACE INTO price_cache
                       (currency, price, timestamp, expires_at)
                       VALUES (?, ?, ?, ?)''',
                    (currency, price, timestamp, expires_at)
                )
            conn.commit()

    def cleanup_old_entries(self):
        """Remove expired cache entries"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'DELETE FROM price_cache WHERE expires_at <= ?', (int(time.time()),))
            conn.commit()

    def get_stats(self) -> Dict:
        """Get cache statistics"""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute(
                'SELECT COUNT(*) FROM price_cache').fetchone()[0]
            valid = conn.execute('SELECT COUNT(*) FROM price_cache WHERE expires_at > ?',
                                 (int(time.time()),)).fetchone()[0]
            return {'total_entries': total, 'valid_entries': valid}


class OKXLoanMonitor:
    def __init__(self, api_key: str, secret_key: str, passphrase: str, flag: str = "0"):
        self.api_key = api_key.strip()
        self.secret_key = secret_key.strip()
        self.passphrase = passphrase.strip()
        self.base_url = "https://www.okx.com"
        self.cache = PriceCache()

        # In-memory cache for current session
        self.price_cache = {}
        self.cache_timestamp = 0
        self.CACHE_DURATION = 30  # Cache prices for 30 seconds

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

            # Respect rate limits - add small delay
            time.sleep(0.1)
            return response.json()

        except requests.exceptions.RequestException as e:
            return {"code": "error", "msg": str(e)}

    def get_account_balance(self) -> Dict:
        """Get trading account balance"""
        return self._request('GET', '/api/v5/account/balance')

    def get_flexible_loan_info(self) -> Dict:
        """Get flexible loan information"""
        return self._request('GET', '/api/v5/finance/flexible-loan/loan-info')

    def get_all_usd_pairs(self) -> Dict[str, float]:
        """Get all USD-based trading pairs in one batch request"""
        current_time = time.time()

        # Use cached prices if they're still fresh
        if self.price_cache and (current_time - self.cache_timestamp) < self.CACHE_DURATION:
            print("  Using session-cached prices...")
            return self.price_cache.copy()

        print("  Fetching all USD pairs (this may take a few seconds)...")
        prices = {}
        usd_quotes = ['USDT', 'USDC', 'USD']
        stablecoins = ['USDT', 'USDC', 'USD', 'BUSD', 'DAI', 'USDP']

        # Add stablecoins
        for stablecoin in stablecoins:
            prices[stablecoin] = 1.0

        # Fetch all spot tickers in batches
        page = 1
        max_pages = 5  # Limit pages to avoid too many requests

        while page <= max_pages:
            try:
                result = self._request(
                    'GET',
                    '/api/v5/market/tickers',
                    {'instType': 'SPOT', 'limit': '100', 'page': str(page)}
                )

                if result.get('code') == '0' and result.get('data'):
                    tickers = result['data']
                    if not tickers:
                        break

                    for ticker in tickers:
                        inst_id = ticker.get('instId', '')
                        last_price = ticker.get('last', '0')

                        if inst_id and last_price:
                            # Check if it's a USD-based pair
                            for quote in usd_quotes:
                                if inst_id.endswith(f"-{quote}"):
                                    # Remove "-QUOTE" suffix
                                    base_ccy = inst_id[:-len(quote)-1]
                                    try:
                                        price_val = float(last_price)
                                        if price_val > 0:
                                            # Only add if not already present or if this is a better quote
                                            if base_ccy not in prices or quote == 'USDT':  # Prefer USDT
                                                prices[base_ccy] = price_val
                                    except (ValueError, TypeError):
                                        continue
                                    break

                    page += 1
                    # Small delay between pages to be respectful of rate limits
                    time.sleep(0.2)
                else:
                    break

            except Exception as e:
                print(f"  Error fetching page {page}: {e}")
                break

        print(f"  Found {len(prices)} USD-based trading pairs")

        # Cache the results in SQLite for future runs (5 minute TTL)
        self.cache.set_batch(prices, ttl=300)

        # Also cache in memory for current session
        self.price_cache = prices.copy()
        self.cache_timestamp = current_time

        return prices

    def get_usd_ticker_prices(self, currencies: list) -> Dict[str, float]:
        """Get current USD prices for multiple currencies using cached data"""
        prices = {}
        cache_stats = self.cache.get_stats()
        print(
            f"  Cache stats: {cache_stats['valid_entries']}/{cache_stats['total_entries']} valid entries")

        # First try to get prices from SQLite cache
        cached_currencies = []
        missing_currencies = []

        for ccy in currencies:
            cached_price = self.cache.get(ccy)
            if cached_price is not None:
                prices[ccy] = cached_price
                cached_currencies.append(ccy)
            else:
                missing_currencies.append(ccy)

        if cached_currencies:
            print(f"  Found {len(cached_currencies)} currencies in cache")

        # If we have missing currencies, try batch API
        if missing_currencies:
            print(
                f"  Fetching {len(missing_currencies)} missing currencies from API...")
            all_usd_pairs = self.get_all_usd_pairs()

            # Match missing currencies with batch data
            newly_found = []
            still_missing = []

            for ccy in missing_currencies:
                if ccy in all_usd_pairs:
                    prices[ccy] = all_usd_pairs[ccy]
                    newly_found.append(ccy)
                else:
                    still_missing.append(ccy)

            if newly_found:
                print(f"    Found {len(newly_found)} currencies in batch API")
                # Cache the newly found prices
                new_prices = {ccy: prices[ccy] for ccy in newly_found}
                self.cache.set_batch(new_prices, ttl=300)

            # Try individual lookups for any remaining missing currencies
            if still_missing:
                print(
                    f"    Looking for {len(still_missing)} currencies individually...")
                usd_quotes = ['USDT', 'USDC', 'USD']

                for ccy in still_missing:
                    if ccy in prices:
                        continue

                    price_found = False
                    for quote in usd_quotes:
                        inst_id = f"{ccy}-{quote}"
                        try:
                            result = self._request(
                                'GET',
                                '/api/v5/market/ticker',
                                {'instId': inst_id}
                            )
                            if result.get('code') == '0' and result.get('data'):
                                last_price = result['data'][0].get('last', '0')
                                if last_price and float(last_price) > 0:
                                    price_val = float(last_price)
                                    prices[ccy] = price_val
                                    print(
                                        f"      Found {ccy}: ${price_val:.8f} via {quote}")
                                    price_found = True

                                    # Add to cache
                                    self.cache.set(ccy, price_val, ttl=300)
                                    break
                        except Exception:
                            continue

                    if not price_found:
                        prices[ccy] = 0.0
                        print(f"      ❌ No USD price found for {ccy}")

        return prices

    def calculate_precise_usd_value(self, amount: float, price: float, currency: str) -> float:
        """Calculate USD value with proper precision handling for low-priced tokens"""
        try:
            # Use Decimal for precise calculation with very small numbers
            amount_dec = Decimal(str(amount))
            price_dec = Decimal(str(price))
            usd_value = float(amount_dec * price_dec)

            # For very low-priced tokens, we might need more decimal places
            if price < 0.0001:  # Very low price tokens like PEPE, SHIB
                # Round to 8 decimal places for very small values
                usd_value = round(usd_value, 8)
            elif price < 0.01:  # Low price tokens
                usd_value = round(usd_value, 6)
            else:
                usd_value = round(usd_value, 2)

            return usd_value
        except:
            return 0.0

    def parse_loan_info(self, loan_data: Dict, prices: Dict[str, float] = None) -> Dict:
        """Parse flexible loan information"""
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

        # Parse collateral with USD values
        collateral_data = loan_info.get('collateralData', [])
        total_calculated_collateral = 0.0

        for item in collateral_data:
            amt = float(item.get('amt', 0))
            ccy = item.get('ccy', '')
            if amt > 0:
                # Calculate USD value if prices provided
                usd_value = 0.0
                if prices and ccy in prices:
                    usd_value = self.calculate_precise_usd_value(
                        amt, prices[ccy], ccy)
                elif ccy in ['USDT', 'USDC', 'USD', 'BUSD', 'DAI', 'USDP']:
                    usd_value = amt  # Stablecoins are 1:1

                collateral_asset = {
                    'currency': ccy,
                    'amount': amt,
                    'usd_value': usd_value
                }
                loan_metrics['collateral_assets'].append(collateral_asset)
                total_calculated_collateral += usd_value

        # Parse loans
        loan_data_list = loan_info.get('loanData', [])
        for item in loan_data_list:
            amt = float(item.get('amt', 0))
            if amt > 0:
                loan_metrics['loan_assets'].append({
                    'currency': item.get('ccy', ''),
                    'amount': amt
                })

        # Parse metrics from API (these are the authoritative values)
        loan_metrics['collateral_usd'] = float(
            loan_info.get('collateralNotionalUsd', 0))
        loan_metrics['loan_usd'] = float(loan_info.get('loanNotionalUsd', 0))
        loan_metrics['current_ltv'] = float(loan_info.get('curLTV', 0)) * 100
        loan_metrics['margin_call_ltv'] = float(
            loan_info.get('marginCallLTV', 0)) * 100
        loan_metrics['liquidation_ltv'] = float(
            loan_info.get('liqLTV', 0)) * 100

        # If our calculated collateral is significantly different from API,
        # it means our prices might be stale/wrong
        api_collateral = loan_metrics['collateral_usd']
        # 10% difference
        if api_collateral > 0 and abs(total_calculated_collateral - api_collateral) > api_collateral * 0.1:
            discrepancy_pct = abs(
                total_calculated_collateral - api_collateral) / api_collateral * 100
            print(
                f"⚠️  Price discrepancy: Our calculation ${total_calculated_collateral:.2f} vs API ${api_collateral:.2f} ({discrepancy_pct:.1f}% difference)")

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
            dust_count = 0
            dust_value = 0.0

            for asset in sorted_collateral:
                usd_val = asset.get('usd_value', 0)

                # Show assets with significant value, group dust
                if usd_val >= 0.01:  # Show assets worth $0.01 or more
                    # For very small USD values, show more decimal places
                    if usd_val < 1.0:
                        usd_display = f"${usd_val:>14,.4f}"
                    else:
                        usd_display = f"${usd_val:>14,.2f}"

                    print(
                        f"  {asset['currency']:<10} {asset['amount']:>20,.8f} {usd_display}")
                    total_shown_usd += usd_val
                    shown_count += 1
                else:
                    dust_count += 1
                    dust_value += usd_val

            if dust_count > 0:
                print(
                    f"  ... and {dust_count} more dust assets (${dust_value:,.4f} total)")

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

        # Get list of currencies from loan collateral for price lookup
        currencies = []
        if loan_info.get('code') == '0' and loan_info.get('data'):
            collateral_data = loan_info['data'][0].get('collateralData', [])
            currencies = [item['ccy'] for item in collateral_data]

        # Fetch prices using optimized caching approach
        if currencies:
            print(f"Fetching USD prices for {len(currencies)} currencies...")
            prices = self.get_usd_ticker_prices(currencies)

            # Debug: Show PEPE price specifically
            if 'PEPE' in prices:
                print(f"  PEPE/USD price: ${prices['PEPE']:.10f}")

        else:
            prices = {}

        account_metrics = self.calculate_account_metrics(balance)
        loan_metrics = self.parse_loan_info(loan_info, prices)

        self.display_combined_metrics(account_metrics, loan_metrics)


def main():
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

    monitor = OKXLoanMonitor(api_key, secret_key, passphrase, flag)
    monitor.run()


if __name__ == "__main__":
    main()
