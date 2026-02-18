#!/usr/bin/env python3
"""QA Certification Script for Deposit Module.

Usage:
    poetry run python tools/test_deposit_certify.py
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Add qa_fixtures to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'qa_fixtures'))

from fake_ledger import FakeLedger


def main():
    print("=" * 60)
    print("QA CERTIFICATION: Deposit Module")
    print("=" * 60)

    os.makedirs('diagnostics/deposit_debug', exist_ok=True)

    passed = 0
    failed = 0

    # Test 1: Happy path - deposit creation
    print("\n[1/5] Happy path - deposit creation...")
    try:
        ledger = FakeLedger(initial_balance=0.0)
        run_id = int(time.time() * 1000)

        result = ledger.create_deposit(
            txid='tx001',
            amount=1.0,
            currency='BTC',
            required_confirmations=1
        )

        if result['txid'] == 'tx001' and result['amount'] == 1.0:
            print(f"  OK: deposit created")
            passed += 1

            # Dump manifest
            ledger.dump_manifest(run_id, 'happy_path_deposit', {'txid': 'tx001', 'amount': 1.0})
        else:
            print(f"  FAIL: unexpected result {result}")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 2: Idempotency - same txid returns same result
    print("\n[2/5] Idempotency by txid...")
    try:
        ledger = FakeLedger(initial_balance=0.0)
        run_id = int(time.time() * 1000)

        result1 = ledger.create_deposit('tx002', 2.0, 'BTC', 1)
        result2 = ledger.create_deposit('tx002', 2.0, 'BTC', 1)

        if result1['idempotent'] == False and result2['idempotent'] == True:
            print(f"  OK: idempotent=true on retry")
            passed += 1
            ledger.dump_manifest(run_id, 'idempotency', {'txid': 'tx002'})
        else:
            print(f"  FAIL: idempotency not working")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 3: Confirmations - insufficient confirmations
    print("\n[3/5] Insufficient confirmations...")
    try:
        ledger = FakeLedger(initial_balance=0.0)
        run_id = int(time.time() * 1000)

        ledger.create_deposit('tx003', 3.0, 'BTC', required_confirmations=3)

        # Try to confirm with only 2 confirmations
        result = ledger.confirm_deposit('tx003', confirmations=2)

        if result['status'] == 'pending':
            print(f"  OK: still pending with 2/3 confirmations")
            passed += 1
            ledger.dump_manifest(run_id, 'insufficient_confirmations', {'txid': 'tx003', 'confirmations': 2})
        else:
            print(f"  FAIL: unexpected status {result['status']}")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 4: Retry without duplicating - balance consistency
    print("\n[4/5] Retry without duplicate (balance consistency)...")
    try:
        ledger = FakeLedger(initial_balance=0.0)
        run_id = int(time.time() * 1000)

        # Create and confirm
        ledger.create_deposit('tx004', 5.0, 'BTC', 1)
        ledger.confirm_deposit('tx004', 1)

        balance_after_first = ledger.get_balance('BTC')

        # Retry - should not duplicate
        ledger.create_deposit('tx004', 5.0, 'BTC', 1)

        balance_after_retry = ledger.get_balance('BTC')

        if balance_after_first == balance_after_retry:
            print(f"  OK: balance consistent after retry ({balance_after_first})")
            passed += 1
            ledger.dump_manifest(run_id, 'retry_no_duplicate', {'txid': 'tx004'})
        else:
            print(f"  FAIL: balance changed on retry: {balance_after_first} -> {balance_after_retry}")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 5: Full flow - create, confirm, verify balance
    print("\n[5/5] Full flow - create, confirm, verify balance...")
    try:
        ledger = FakeLedger(initial_balance=10.0)
        run_id = int(time.time() * 1000)

        # Create deposit
        create_result = ledger.create_deposit('tx005', 5.0, 'BTC', 1)
        balance_after_create = ledger.get_balance('BTC')

        # Confirm deposit
        confirm_result = ledger.confirm_deposit('tx005', 1)
        balance_after_confirm = ledger.get_balance('BTC')

        if (balance_after_create == 10.0 and
            balance_after_confirm == 15.0 and
            confirm_result['balance_after'] == 15.0):
            print(f"  OK: full flow works (10 -> 15)")
            passed += 1
            ledger.dump_manifest(run_id, 'full_flow', {'txid': 'tx005'})
        else:
            print(f"  FAIL: balance mismatch: create={balance_after_create}, confirm={balance_after_confirm}")
            failed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    print("\n" + "=" * 60)
    print(f"RESULT: {passed} passed, {failed} failed")
    print("=" * 60)

    # List generated manifests
    print("\nGenerated manifests:")
    debug_dir = Path('diagnostics/deposit_debug')
    if debug_dir.exists():
        for d in sorted(debug_dir.iterdir())[:5]:
            manifest = d / 'manifest.json'
            if manifest.exists():
                print(f"  {manifest}")

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
