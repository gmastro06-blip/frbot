#!/usr/bin/env python3
"""QA Certification Script for Trade Module.

Usage:
    poetry run python tools/test_trade_certify.py
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Add qa_fixtures to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'qa_fixtures'))

from fake_ledger import FakeExchange


def main():
    print("=" * 60)
    print("QA CERTIFICATION: Trade Module")
    print("=" * 60)

    os.makedirs('diagnostics/trade_debug', exist_ok=True)

    passed = 0
    failed = 0

    # Test 1: Happy path - limit order
    print("\n[1/8] Happy path - limit buy order...")
    try:
        exchange = FakeExchange(initial_balance_quote=10000.0, initial_balance_base=0.0)
        run_id = int(time.time() * 1000)

        result = exchange.create_order(
            order_id='order001',
            side='buy',
            symbol='BTC/USDT',
            quantity=0.1,
            price=50000.0
        )

        if result['order_id'] == 'order001' and result['status'] == 'pending':
            print(f"  OK: limit order created")
            passed += 1
            exchange.dump_manifest(run_id, 'limit_order', {'order_id': 'order001'})
        else:
            print(f"  FAIL: unexpected result {result}")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 2: Market order
    print("\n[2/8] Market order - immediate fill...")
    try:
        exchange = FakeExchange(initial_balance_quote=10000.0, initial_balance_base=0.0)
        run_id = int(time.time() * 1000)

        result = exchange.create_order(
            order_id='order002',
            side='buy',
            symbol='BTC/USDT',
            quantity=0.1,
            price=None  # Market order
        )

        if result['status'] == 'filled':
            print(f"  OK: market order filled")
            passed += 1
            exchange.dump_manifest(run_id, 'market_order', {'order_id': 'order002'})
        else:
            print(f"  FAIL: expected filled, got {result['status']}")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 3: Partial fill
    print("\n[3/8] Partial fills...")
    try:
        exchange = FakeExchange(initial_balance_quote=10000.0, initial_balance_base=0.0)
        run_id = int(time.time() * 1000)

        # Create pending limit order
        exchange.create_order('order003', 'buy', 'BTC/USDT', 0.1, 50000.0)

        # Fill partial
        result = exchange.fill_order('order003', 0.05)

        if result['filled_quantity'] == 0.05 and result['status'] == 'partial':
            print(f"  OK: partial fill works")
            passed += 1
            exchange.dump_manifest(run_id, 'partial_fill', {'order_id': 'order003'})
        else:
            print(f"  FAIL: unexpected fill result {result}")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 4: Cancel order
    print("\n[4/8] Cancel order...")
    try:
        exchange = FakeExchange(initial_balance_quote=10000.0, initial_balance_base=0.0)
        run_id = int(time.time() * 1000)

        exchange.create_order('order004', 'buy', 'BTC/USDT', 0.1, 50000.0)
        result = exchange.cancel_order('order004')

        if result['status'] == 'cancelled':
            print(f"  OK: order cancelled")
            passed += 1
            exchange.dump_manifest(run_id, 'cancel_order', {'order_id': 'order004'})
        else:
            print(f"  FAIL: expected cancelled, got {result['status']}")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 5: Insufficient funds (buy side)
    print("\n[5/8] Insufficient funds (buy)...")
    try:
        exchange = FakeExchange(initial_balance_quote=100.0, initial_balance_base=0.0)  # Only 100 USDT
        run_id = int(time.time() * 1000)

        result = exchange.create_order(
            order_id='order005',
            side='buy',
            symbol='BTC/USDT',
            quantity=0.1,
            price=50000.0  # Requires 5000 USDT
        )

        if result['status'] == 'rejected':
            print(f"  OK: order rejected due to insufficient funds")
            passed += 1
            exchange.dump_manifest(run_id, 'insufficient_funds_buy', {'order_id': 'order005'})
        else:
            print(f"  FAIL: expected rejected, got {result['status']}")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 6: Fee calculation
    print("\n[6/8] Fee calculation...")
    try:
        exchange = FakeExchange(initial_balance_quote=10000.0, initial_balance_base=0.0)
        run_id = int(time.time() * 1000)

        result = exchange.create_order(
            order_id='order006',
            side='buy',
            symbol='BTC/USDT',
            quantity=1.0,  # 1 BTC
            price=None  # Market @ 1000
        )

        # 1 BTC * 1000 * 0.1% fee = 1.0
        expected_fee = 1.0
        if result['fee'] == expected_fee:
            print(f"  OK: fee calculated correctly ({result['fee']})")
            passed += 1
            exchange.dump_manifest(run_id, 'fee_calculation', {'order_id': 'order006'})
        else:
            print(f"  FAIL: expected fee {expected_fee}, got {result['fee']}")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 7: Idempotency - retry order creation
    print("\n[7/8] Idempotency (retry without duplicate)...")
    try:
        exchange = FakeExchange(initial_balance_quote=10000.0, initial_balance_base=0.0)
        run_id = int(time.time() * 1000)

        result1 = exchange.create_order('order007', 'buy', 'BTC/USDT', 0.1, 50000.0)
        result2 = exchange.create_order('order007', 'buy', 'BTC/USDT', 0.1, 50000.0)

        if result1['idempotent'] == False and result2['idempotent'] == True:
            print(f"  OK: idempotent=true on retry")
            passed += 1
            exchange.dump_manifest(run_id, 'idempotency', {'order_id': 'order007'})
        else:
            print(f"  FAIL: idempotency not working")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 8: Rounding - quantity precision
    print("\n[8/8] Rounding and precision...")
    try:
        exchange = FakeExchange(initial_balance_quote=10000.0, initial_balance_base=0.0)
        run_id = int(time.time() * 1000)

        # Create and fill with precision
        exchange.create_order('order008', 'buy', 'BTC/USDT', 0.12345678, 50000.0)
        result = exchange.fill_order('order008', 0.12345678)

        if result['filled_quantity'] == 0.12345678:
            print(f"  OK: precision maintained")
            passed += 1
            exchange.dump_manifest(run_id, 'rounding_precision', {'order_id': 'order008'})
        else:
            print(f"  FAIL: precision lost: {result['filled_quantity']}")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    print("\n" + "=" * 60)
    print(f"RESULT: {passed} passed, {failed} failed")
    print("=" * 60)

    # List generated manifests
    print("\nGenerated manifests:")
    debug_dir = Path('diagnostics/trade_debug')
    if debug_dir.exists():
        for d in sorted(debug_dir.iterdir())[:5]:
            manifest = d / 'manifest.json'
            if manifest.exists():
                print(f"  {manifest}")

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
