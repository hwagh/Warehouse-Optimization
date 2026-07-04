"""
Warehouse Capacity Planner — Configuration & Data Models (per-area draw model)

Primary storage areas:  Dock | Back wall | Bulk | Mez | Packouts
Order types: BE | BW | CX | DR | SB | SO (Primary, all areas) | SR | SW

Each order type consumes storage in SOME areas, not all. Every order type
carries an explicit per-area draw: boxes ONE order places into each area.
A draw of 0 means that order type does not touch that area.

    load_boxes(area) = Σ orders: order.daily_volume × multiplier × order.draw[area]
    utilization_pct  = load_boxes / capacity_boxes × 100
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


AREA_ORDER: List[str] = ["dock", "back", "bulk", "mez", "pack"]
AREA_NAMES: Dict[str, str] = {
    "dock": "Dock", "back": "Back wall", "bulk": "Bulk",
    "mez": "Mez", "pack": "Packouts",
}


@dataclass
class StorageArea:
    id: str
    name: str
    rack_length_cuft: float
    rack_depth_cuft:  float
    rack_height_cuft: float
    num_racks:        int
    efficiency:       float
    units_per_box:    float
    box_length_cuft:  float
    box_depth_cuft:   float
    box_height_cuft:  float
    max_concurrent_boxes: Optional[int] = None

    @property
    def avg_box_size_cuft(self) -> float:
        return self.box_length_cuft * self.box_depth_cuft * self.box_height_cuft

    @property
    def volume_cuft(self) -> float:
        return self.rack_length_cuft * self.rack_depth_cuft * self.rack_height_cuft * self.num_racks

    @property
    def rack_volume_cuft(self) -> float:
        return self.rack_length_cuft * self.rack_depth_cuft * self.rack_height_cuft

    @property
    def capacity_boxes(self) -> int:
        box = self.avg_box_size_cuft
        vol_cap = int((self.volume_cuft * self.efficiency) / box) if box > 0 else 0
        if self.max_concurrent_boxes is not None:
            return min(self.max_concurrent_boxes, vol_cap) if vol_cap > 0 else self.max_concurrent_boxes
        return vol_cap

    @property
    def capacity_units(self) -> int:
        return int(self.capacity_boxes * self.units_per_box)

    @property
    def has_box_cap(self) -> bool:
        return self.max_concurrent_boxes is not None

    def utilization_pct(self, load_boxes: float) -> float:
        cap = self.capacity_boxes
        return (load_boxes / cap * 100) if cap > 0 else 0.0

    def status(self, load_boxes: float) -> str:
        pct = self.utilization_pct(load_boxes)
        if pct >= 100: return "OVER CAPACITY"
        if pct >= 85:  return "CRITICAL"
        if pct >= 70:  return "WARNING"
        return "OK"


@dataclass
class OrderType:
    id: str
    name: str
    daily_volume: int
    avg_units_per_order: int
    draw: Dict[str, float] = field(default_factory=dict)

    def draw_for(self, area_id: str) -> float:
        return float(self.draw.get(area_id, 0.0))

    def affects(self, area_id: str) -> bool:
        return self.draw_for(area_id) > 0.0

    def boxes_in_area(self, area: "StorageArea", multiplier: float = 1.0) -> float:
        return self.daily_volume * multiplier * self.draw_for(area.id)

    @property
    def affected_areas(self) -> List[str]:
        return [aid for aid in AREA_ORDER if self.affects(aid)]


def _area(id, name, cap, upb):
    return StorageArea(
        id=id, name=name,
        rack_length_cuft=10.0, rack_depth_cuft=4.0, rack_height_cuft=8.0,
        num_racks=max(1, int(cap / 20) + 10), efficiency=0.70,
        units_per_box=upb,
        box_length_cuft=1.0, box_depth_cuft=1.0, box_height_cuft=1.0,
        max_concurrent_boxes=cap,
    )

DEFAULT_AREAS: List[StorageArea] = [
    _area("dock", "Dock",      1200, 60),
    _area("back", "Back wall", 3000, 12),
    _area("bulk", "Bulk",      2600, 8),
    _area("mez",  "Mez",       1800, 8),
    _area("pack", "Packouts",  2000, 6),
]


def _ot(id, name, vol, upo, draw):
    return OrderType(id=id, name=name, daily_volume=vol, avg_units_per_order=upo, draw=draw)

DEFAULT_ORDER_TYPES: List[OrderType] = [
    _ot("SO", "SO – Primary order", 120, 50,
        {"dock": 0.8, "back": 2.5, "bulk": 1.6, "mez": 1.1, "pack": 1.4}),
    _ot("BE", "BE – Bulk orders",    40, 35,
        {"dock": 1.5, "back": 0.0, "bulk": 2.0, "mez": 0.0, "pack": 0.9}),
    _ot("BW", "BW",                  25, 120,
        {"dock": 2.4, "back": 0.0, "bulk": 3.0, "mez": 0.0, "pack": 0.6}),
    _ot("CX", "CX – Customer service", 16, 129,
        {"dock": 0.0, "back": 1.2, "bulk": 0.0, "mez": 0.0, "pack": 0.8}),
    _ot("DR", "DR",                  17, 112,
        {"dock": 0.0, "back": 1.4, "bulk": 0.0, "mez": 1.0, "pack": 1.1}),
    _ot("SB", "SB",                  12, 20,
        {"dock": 0.0, "back": 0.0, "bulk": 1.3, "mez": 0.9, "pack": 0.7}),
    _ot("SR", "SR",                   8, 18,
        {"dock": 0.0, "back": 1.1, "bulk": 0.0, "mez": 0.8, "pack": 0.6}),
    _ot("SW", "SW",                  30, 22,
        {"dock": 0.0, "back": 1.8, "bulk": 1.2, "mez": 0.0, "pack": 0.9}),
]
