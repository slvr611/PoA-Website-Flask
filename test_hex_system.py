#!/usr/bin/env python3
"""
Test script for the hexagonal coordinate system.
This script tests the hex coordinate utilities without requiring database connection.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from helpers.map_helpers import (
    HexCoordinate, hex_distance, hex_neighbors, 
    hex_to_pixel, pixel_to_hex, hex_round, create_hex_grid
)

def test_hex_coordinate_creation():
    """Test HexCoordinate creation and validation."""
    print("Testing HexCoordinate creation...")
    
    # Valid coordinates
    try:
        hex1 = HexCoordinate(1, -1, 0)
        print(f"✓ Created valid coordinate: {hex1}")
    except ValueError as e:
        print(f"✗ Failed to create valid coordinate: {e}")
        return False
    
    # Auto-calculate s coordinate
    try:
        hex2 = HexCoordinate(2, -1)  # s should be -1
        print(f"✓ Auto-calculated s coordinate: {hex2}")
        assert hex2.s == -1, f"Expected s=-1, got s={hex2.s}"
    except (ValueError, AssertionError) as e:
        print(f"✗ Failed auto-calculation test: {e}")
        return False
    
    # Invalid coordinates should raise error
    try:
        hex3 = HexCoordinate(1, 1, 1)  # q+r+s = 3, should fail
        print(f"✗ Should have failed to create invalid coordinate: {hex3}")
        return False
    except ValueError:
        print("✓ Correctly rejected invalid coordinates")
    
    return True

def test_hex_distance():
    """Test distance calculation between hexagons."""
    print("\nTesting hex distance calculation...")
    
    origin = HexCoordinate(0, 0, 0)
    
    # Distance to adjacent hexes should be 1
    adjacent = HexCoordinate(1, 0, -1)
    dist = hex_distance(origin, adjacent)
    if dist == 1:
        print(f"✓ Distance to adjacent hex: {dist}")
    else:
        print(f"✗ Expected distance 1, got {dist}")
        return False
    
    # Distance to hex 2 steps away should be 2
    far = HexCoordinate(2, 0, -2)
    dist = hex_distance(origin, far)
    if dist == 2:
        print(f"✓ Distance to far hex: {dist}")
    else:
        print(f"✗ Expected distance 2, got {dist}")
        return False
    
    # Distance to self should be 0
    dist = hex_distance(origin, origin)
    if dist == 0:
        print(f"✓ Distance to self: {dist}")
    else:
        print(f"✗ Expected distance 0, got {dist}")
        return False
    
    return True

def test_hex_neighbors():
    """Test neighbor finding."""
    print("\nTesting hex neighbors...")
    
    origin = HexCoordinate(0, 0, 0)
    neighbors = hex_neighbors(origin)
    
    if len(neighbors) == 6:
        print(f"✓ Found 6 neighbors")
    else:
        print(f"✗ Expected 6 neighbors, got {len(neighbors)}")
        return False
    
    # All neighbors should be distance 1 from origin
    for i, neighbor in enumerate(neighbors):
        dist = hex_distance(origin, neighbor)
        if dist != 1:
            print(f"✗ Neighbor {i} at distance {dist}, expected 1")
            return False
    
    print("✓ All neighbors are at distance 1")
    
    # Check specific neighbor coordinates
    expected_neighbors = [
        (1, 0, -1), (1, -1, 0), (0, -1, 1),
        (-1, 0, 1), (-1, 1, 0), (0, 1, -1)
    ]
    
    neighbor_coords = [(n.q, n.r, n.s) for n in neighbors]
    for expected in expected_neighbors:
        if expected not in neighbor_coords:
            print(f"✗ Missing expected neighbor: {expected}")
            return False
    
    print("✓ All expected neighbors found")
    return True

def test_pixel_conversion():
    """Test hex-to-pixel and pixel-to-hex conversion."""
    print("\nTesting pixel conversion...")
    
    origin = HexCoordinate(0, 0, 0)
    size = 20
    
    # Convert to pixel and back
    x, y = hex_to_pixel(origin, size)
    converted_back = pixel_to_hex(x, y, size)
    
    if origin == converted_back:
        print(f"✓ Round-trip conversion successful: {origin} -> ({x:.2f}, {y:.2f}) -> {converted_back}")
    else:
        print(f"✗ Round-trip conversion failed: {origin} -> {converted_back}")
        return False
    
    # Test a few more coordinates
    test_coords = [
        HexCoordinate(1, 0, -1),
        HexCoordinate(0, 1, -1),
        HexCoordinate(-1, 1, 0),
        HexCoordinate(2, -1, -1)
    ]
    
    for coord in test_coords:
        x, y = hex_to_pixel(coord, size)
        converted = pixel_to_hex(x, y, size)
        if coord != converted:
            print(f"✗ Conversion failed for {coord}: got {converted}")
            return False
    
    print("✓ All coordinate conversions successful")
    return True

def test_hex_round():
    """Test hex coordinate rounding."""
    print("\nTesting hex rounding...")
    
    # Test exact coordinates (should remain unchanged)
    exact = hex_round(1.0, 0.0)
    expected = HexCoordinate(1, 0, -1)
    if exact == expected:
        print(f"✓ Exact coordinate rounding: {exact}")
    else:
        print(f"✗ Exact coordinate rounding failed: expected {expected}, got {exact}")
        return False
    
    # Test fractional coordinates
    fractional = hex_round(1.4, -0.6)  # Should round to (1, -1, 0)
    expected = HexCoordinate(1, -1, 0)
    if fractional == expected:
        print(f"✓ Fractional coordinate rounding: {fractional}")
    else:
        print(f"✗ Fractional coordinate rounding failed: expected {expected}, got {fractional}")
        return False
    
    return True

def test_hex_grid_creation():
    """Test hex grid creation."""
    print("\nTesting hex grid creation...")
    
    # Create a small 3x3 grid
    grid = create_hex_grid(3, 3, 0, 0)
    
    if len(grid) == 9:
        print(f"✓ Created grid with {len(grid)} hexes")
    else:
        print(f"✗ Expected 9 hexes, got {len(grid)}")
        return False
    
    # Check that all coordinates are valid
    for hex_coord in grid:
        if hex_coord.q + hex_coord.r + hex_coord.s != 0:
            print(f"✗ Invalid coordinate in grid: {hex_coord}")
            return False
    
    print("✓ All grid coordinates are valid")
    return True

def run_all_tests():
    """Run all hex system tests."""
    print("=" * 50)
    print("HEXAGONAL COORDINATE SYSTEM TESTS")
    print("=" * 50)
    
    tests = [
        test_hex_coordinate_creation,
        test_hex_distance,
        test_hex_neighbors,
        test_pixel_conversion,
        test_hex_round,
        test_hex_grid_creation
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                print("Test failed!")
        except Exception as e:
            print(f"Test error: {e}")
    
    print("\n" + "=" * 50)
    print(f"RESULTS: {passed}/{total} tests passed")
    print("=" * 50)
    
    if passed == total:
        print("🎉 All tests passed! The hex system is working correctly.")
        return True
    else:
        print("❌ Some tests failed. Please check the implementation.")
        return False

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
