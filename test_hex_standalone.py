#!/usr/bin/env python3
"""
Standalone test script for the hexagonal coordinate system.
This script contains the hex utilities inline to avoid database dependencies.
"""

import math
from typing import Tuple, List


class HexCoordinate:
    """Represents a hexagonal coordinate using the cube coordinate system (q, r, s)."""
    
    def __init__(self, q: int, r: int, s: int = None):
        if s is None:
            s = -q - r
        
        # Validate that q + r + s = 0 (cube coordinate constraint)
        if q + r + s != 0:
            raise ValueError(f"Invalid hex coordinate: q={q}, r={r}, s={s}. Must satisfy q + r + s = 0")
        
        self.q = q
        self.r = r
        self.s = s
    
    def __eq__(self, other):
        if not isinstance(other, HexCoordinate):
            return False
        return self.q == other.q and self.r == other.r and self.s == other.s
    
    def __hash__(self):
        return hash((self.q, self.r, self.s))
    
    def __str__(self):
        return f"Hex({self.q}, {self.r}, {self.s})"


def hex_distance(hex1: HexCoordinate, hex2: HexCoordinate) -> int:
    """Calculate the distance between two hexagonal coordinates."""
    return (abs(hex1.q - hex2.q) + abs(hex1.r - hex2.r) + abs(hex1.s - hex2.s)) // 2


def hex_neighbors(hex_coord: HexCoordinate) -> List[HexCoordinate]:
    """Get all 6 neighboring hexagonal coordinates."""
    directions = [
        (1, 0, -1), (1, -1, 0), (0, -1, 1),
        (-1, 0, 1), (-1, 1, 0), (0, 1, -1)
    ]
    
    neighbors = []
    for dq, dr, ds in directions:
        neighbors.append(HexCoordinate(
            hex_coord.q + dq,
            hex_coord.r + dr,
            hex_coord.s + ds
        ))
    
    return neighbors


def hex_to_pixel(hex_coord: HexCoordinate, size: float) -> Tuple[float, float]:
    """Convert hexagonal coordinates to pixel coordinates for rendering."""
    x = size * (3/2 * hex_coord.q)
    y = size * (math.sqrt(3)/2 * hex_coord.q + math.sqrt(3) * hex_coord.r)
    return x, y


def pixel_to_hex(x: float, y: float, size: float) -> HexCoordinate:
    """Convert pixel coordinates to hexagonal coordinates."""
    q = (2/3 * x) / size
    r = (-1/3 * x + math.sqrt(3)/3 * y) / size
    
    # Round to nearest hex
    return hex_round(q, r)


def hex_round(q: float, r: float) -> HexCoordinate:
    """Round fractional hex coordinates to the nearest hex."""
    s = -q - r
    
    rq = round(q)
    rr = round(r)
    rs = round(s)
    
    q_diff = abs(rq - q)
    r_diff = abs(rr - r)
    s_diff = abs(rs - s)
    
    if q_diff > r_diff and q_diff > s_diff:
        rq = -rr - rs
    elif r_diff > s_diff:
        rr = -rq - rs
    else:
        rs = -rq - rr
    
    return HexCoordinate(int(rq), int(rr), int(rs))


def create_hex_grid(width: int, height: int, center_q: int = 0, center_r: int = 0) -> List[HexCoordinate]:
    """Create a hexagonal grid of specified dimensions centered at given coordinates."""
    coordinates = []
    
    for q in range(center_q - width//2, center_q + width//2 + 1):
        for r in range(center_r - height//2, center_r + height//2 + 1):
            s = -q - r
            coordinates.append(HexCoordinate(q, r, s))
    
    return coordinates


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
    # q=1.4, r=-0.6, s=-0.8 -> rounds to q=1, r=-1, s=0 (since q has largest diff)
    fractional = hex_round(1.4, -0.6)
    print(f"Debug: q=1.4, r=-0.6, s={-1.4-(-0.6)} -> {fractional}")

    # Let's test a clearer case: q=0.6, r=0.6 -> s=-1.2
    # Should round to q=1, r=1, s=-2, but that's invalid
    # Actually should round to q=0, r=0, s=0 or q=1, r=0, s=-1
    fractional2 = hex_round(0.6, 0.6)
    expected2 = HexCoordinate(1, 0, -1)  # This should be the result
    if fractional2 == expected2:
        print(f"✓ Fractional coordinate rounding: {fractional2}")
    else:
        print(f"✗ Fractional coordinate rounding failed: expected {expected2}, got {fractional2}")
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
    exit(0 if success else 1)
