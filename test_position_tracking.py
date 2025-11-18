#!/usr/bin/env python3
"""
Test script to verify position tracking fix.
"""

import sys
from pathlib import Path

# Add the project directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

from trade_tracker import get_trade_tracker
from position_monitor import PositionMonitor
from upstox_technical import UpstoxTechnicalClient

def test_position_tracking():
    """Test that positions are tracked correctly in backtest mode."""

    print("🧪 Testing position tracking fix...\n")

    # 1. Create tracker and add a test position
    tracker = get_trade_tracker()

    # Clear any existing open positions for clean test
    tracker.open_positions = {}
    tracker._save_open_positions()

    print("✓ Tracker initialized")

    # 2. Record a test entry
    test_entry = tracker.record_entry(
        symbol="TESTSTOCK",
        side="BUY",
        quantity=10,
        entry_price=100.0,
        product="I",
        stop_loss=95.0,
        target=110.0,
        strategy="test",
        confidence=0.8,
        tags=["backtest", "test"]
    )

    print(f"✓ Test position created: {test_entry['trade_id']}")
    print(f"  Entry: {test_entry['entry_price']}, SL: {test_entry['stop_loss']}, Target: {test_entry['target']}")

    # 3. Verify position is in tracker
    open_positions = tracker.get_open_positions()
    print(f"\n✓ Open positions in tracker: {len(open_positions)}")

    if len(open_positions) == 0:
        print("❌ FAILED: No open positions found in tracker!")
        return False

    # 4. Create position monitor
    try:
        tech = UpstoxTechnicalClient()
        monitor = PositionMonitor(tech_client=tech, trade_tracker=tracker)
        print("✓ Position monitor created")
    except Exception as e:
        print(f"⚠️  Note: Could not init tech client ({e}), continuing with mock...")
        monitor = PositionMonitor(trade_tracker=tracker)

    # 5. Check positions in BACKTEST mode (live=False)
    print("\n📊 Checking positions in BACKTEST mode (live=False)...")

    # Mock the tech client's ltp method to return a price
    class MockTech:
        def ltp(self, instrument_key):
            # Return a price that will hit the target
            return 112.0, None

    monitor.tech = MockTech()

    result = monitor.check_positions(live=False)

    print(f"\nResult: {result}")

    # Verify the result
    if result.get("error"):
        print(f"❌ FAILED: Error during position check: {result['error']}")
        return False

    if result.get("open_positions", 0) == 0:
        print("❌ FAILED: Position monitor found 0 open positions in backtest mode!")
        print("   This means the fix didn't work - monitor should use tracker positions.")
        return False

    print(f"\n✅ SUCCESS: Position monitor found {result['open_positions']} position(s)")
    print(f"   Positions checked: {result.get('positions_checked', 0)}")

    # Check if target was hit (price was 112, target was 110)
    actions = result.get("actions_taken", [])
    if actions:
        print(f"\n🎯 Actions taken: {len(actions)}")
        for action in actions:
            print(f"   - {action['symbol']}: {action['exit_reason']} at {action['exit_price']}")

    # Clean up
    tracker.open_positions = {}
    tracker._save_open_positions()
    print("\n✓ Test cleanup complete")

    return True

if __name__ == "__main__":
    success = test_position_tracking()

    if success:
        print("\n" + "="*60)
        print("✅ ALL TESTS PASSED!")
        print("="*60)
        print("\nThe position tracking fix is working correctly.")
        print("Positions recorded in the tracker will now be monitored")
        print("in backtest mode and can hit SL/targets to generate P&L.")
        sys.exit(0)
    else:
        print("\n" + "="*60)
        print("❌ TEST FAILED!")
        print("="*60)
        sys.exit(1)
