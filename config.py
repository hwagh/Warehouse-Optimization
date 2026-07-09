"""
Warehouse Capacity Planner - Configuration & Data Models

Areas: 600 Paper | 400 Consumables | 300 Cust.Specific 1 | 200 Cust.Specific 2 | 100 Final
Volume is calculated from rack dimensions: L × D × H × num_racks

Flow rules:
  600 → 300 or 200 (paper split per order)
  400 → 300 or 200 (customer split per order)
  300 / 200 → 100 Final (packout)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Zone definitions
# ---------------------------------------------------------------------------
ZONE_NAMES: Dict[str, str] = {
    "600": "Paper",
    "400": "Consumables",
    "300": "Customer Specific 1",
    "200": "Customer Specific 2",
    "100": "Final (ready to ship)",
}

ZONE_FLOW_ORDER = ["600", "400", "300", "200", "100"]


# ---------------------------------------------------------------------------
# StorageArea — volume derived from rack dimensions
# ---------------------------------------------------------------------------
@dataclass
class StorageArea:
    id: str
    name: str
    zone: str
    # Rack dimensions (in the current display unit, stored in cu ft internally)
    rack_length_cuft: float       # length of one rack (cu ft)
    rack_depth_cuft:  float       # depth of one rack (cu ft)
    rack_height_cuft: float       # height of one rack (cu ft)
    num_racks:        int          # number of racks in this area
    efficiency:       float        # usable fraction 0–1
    units_per_box:      float      # avg units per box in this area
    box_length_cuft:    float      # box length (cu ft)
    box_depth_cuft:     float      # box depth  (cu ft)
    box_height_cuft:    float      # box height (cu ft)

    @property
    def avg_box_size_cuft(self) -> float:
        """Box volume = L × D × H."""
        return self.box_length_cuft * self.box_depth_cuft * self.box_height_cuft

    @property
    def volume_cuft(self) -> float:
        """Total cubic feet = L × D × H × num_racks."""
        return self.rack_length_cuft * self.rack_depth_cuft * self.rack_height_cuft * self.num_racks

    @property
    def rack_volume_cuft(self) -> float:
        """Volume of a single rack."""
        return self.rack_length_cuft * self.rack_depth_cuft * self.rack_height_cuft

    @property
    def capacity_boxes(self) -> int:
        """Capacity is always volume-based: (usable volume) / (box volume)."""
        box = self.avg_box_size_cuft
        if box <= 0:
            return 0
        return int((self.volume_cuft * self.efficiency) / box)

    @property
    def capacity_units(self) -> int:
        return int(self.capacity_boxes * self.units_per_box)

    def utilization_pct(self, load_boxes: float) -> float:
        cap = self.capacity_boxes
        return (load_boxes / cap * 100) if cap > 0 else 0.0

    def status(self, load_boxes: float) -> str:
        pct = self.utilization_pct(load_boxes)
        if pct >= 100: return "OVER CAPACITY"
        if pct >= 85:  return "CRITICAL"
        if pct >= 70:  return "WARNING"
        return "OK"


# ---------------------------------------------------------------------------
# Order splits
# ---------------------------------------------------------------------------
@dataclass
class StorageSplit:
    """Split 1 — paper_pct + consumable_pct = 100."""
    paper_pct:      float = 50.0
    consumable_pct: float = 50.0


@dataclass
class CustomerSplit:
    """Split 2 — cust1_pct + cust2_pct = 100 (applied to consumable portion)."""
    cust1_pct: float = 60.0
    cust2_pct: float = 40.0


@dataclass
class KittingSplit:
    """Split 3 — packout_pct + kitting_pct = 100 (of 300/200 material)."""
    packout_pct: float = 70.0
    kitting_pct: float = 30.0


# ---------------------------------------------------------------------------
# OrderType
# ---------------------------------------------------------------------------
@dataclass
class OrderType:
    id: str
    name: str
    daily_volume: int
    avg_units_per_order: int
    storage_split:  StorageSplit  = field(default_factory=StorageSplit)
    customer_split: CustomerSplit = field(default_factory=CustomerSplit)
    kitting_split:  KittingSplit  = field(default_factory=KittingSplit)

    def total_units(self, multiplier: float = 1.0) -> float:
        return self.daily_volume * multiplier * self.avg_units_per_order

    def units_paper(self, multiplier: float = 1.0) -> float:
        return self.total_units(multiplier) * (self.storage_split.paper_pct / 100)

    def units_consumable(self, multiplier: float = 1.0) -> float:
        return self.total_units(multiplier) * (self.storage_split.consumable_pct / 100)

    def units_cust1(self, multiplier: float = 1.0) -> float:
        return self.units_consumable(multiplier) * (self.customer_split.cust1_pct / 100)

    def units_cust2(self, multiplier: float = 1.0) -> float:
        return self.units_consumable(multiplier) * (self.customer_split.cust2_pct / 100)

    def boxes_in_area(self, area: "StorageArea", multiplier: float = 1.0) -> float:
        """Boxes placed in a given area based on zone routing."""
        if area.zone == "600":
            units = self.units_paper(multiplier)
        elif area.zone == "400":
            units = self.units_consumable(multiplier)
        elif area.zone == "300":
            units = self.units_cust1(multiplier)
        elif area.zone == "200":
            units = self.units_cust2(multiplier)
        else:
            units = 0.0
        return units / area.units_per_box if area.units_per_box > 0 else 0.0


# ---------------------------------------------------------------------------
# Default areas — calibrated from the section capacity workbooks
# (Sojo_Capacity_Project + per-section 200/300/400/600 sheets).
#
# All dimensions are internal cubic feet (cm → ft ÷ 30.48, m³ × 35.3147).
# For each audited section the rack envelope (rack L×D×H × num_racks) and
# efficiency reproduce the workbook's raw volume and mean box efficiency, and
# Capacity is computed purely from volume: (rack L×D×H × num_racks × efficiency)
# ÷ box volume. The audited box/pallet totals below are reference only — the
# rack envelope and efficiency are set so the volume model reproduces them:
#
#   Section 400 — 24 racks × 210 cells, 6,440.8 m³, 72.9% eff → 193,991 boxes
#   Section 600 — 6 pallet bays (2 levels), 1,502.9 m³, 73.4% eff → 538 pallets
#   Section 300 — 77 racks, 1,160 cells, 1,134.7 m³ → 7,512 boxes
#                 (integer-fit of the 60×40×35 cm rep box, same method as 200)
#   Section 200 — 16 std racks + 13 columns, 346 cells → 3,304 boxes
#                 (audited; loose single column still excluded — TBD)
#   Packout (100) — no capacity workbook provided; values remain estimates.
# ---------------------------------------------------------------------------
DEFAULT_AREAS: List[StorageArea] = [
    StorageArea(
        # 6 pallet bays, lower + upper level; 121.92×101.6×160 cm rep pallet
        id="zone600", name="600 – Paper", zone="600",
        rack_length_cuft=98.739, rack_depth_cuft=4.757, rack_height_cuft=18.832,
        num_racks=6, efficiency=0.734,
        box_length_cuft=4.0, box_depth_cuft=3.333, box_height_cuft=5.249, units_per_box=60.0,
    ),
    StorageArea(
        # 24 racks × 210 cells (274×44×106 cm); ~38.49 boxes/cell (40×30×20 cm rep box)
        id="zone400", name="400 – Consumables", zone="400",
        rack_length_cuft=188.780, rack_depth_cuft=3.478, rack_height_cuft=14.436,
        num_racks=24, efficiency=0.729,
        box_length_cuft=1.312, box_depth_cuft=0.984, box_height_cuft=0.656, units_per_box=12.0,
    ),
    StorageArea(
        # 77 racks / 1,160 cells; 60×40×35 cm rep box (51-box sample)
        id="zone300", name="300 – Customer Specific 1", zone="300",
        rack_length_cuft=36.474, rack_depth_cuft=1.804, rack_height_cuft=7.907,
        num_racks=77, efficiency=0.58,
        box_length_cuft=1.969, box_depth_cuft=1.312, box_height_cuft=1.148, units_per_box=8.0,
    ),
    StorageArea(
        # Group A (16 racks×5 cols) + Group B (13 columns); 346 cells; 60×40×35 cm rep box
        id="zone200", name="200 – Customer Specific 2", zone="200",
        rack_length_cuft=46.531, rack_depth_cuft=3.478, rack_height_cuft=7.710,
        num_racks=16, efficiency=0.55,
        box_length_cuft=1.969, box_depth_cuft=1.312, box_height_cuft=1.148, units_per_box=8.0,
    ),
    StorageArea(
        # Section 100 — 32 racks × 39 cells (1,248 cells), 1,087 m³ raw volume.
        # Capacity 8,118 boxes = 6,165 lower (firm field counts) + 1,953 upper
        # (estimate, pending confirmation). Rep box = "small white" 32.5×45×55 cm.
        id="packout", name="Packout – Final Assembly", zone="100",
        rack_length_cuft=85.859, rack_depth_cuft=1.739, rack_height_cuft=8.038,
        num_racks=32, efficiency=0.65,
        box_length_cuft=1.066, box_depth_cuft=1.476, box_height_cuft=1.804, units_per_box=6.0,
    ),
]

# ---------------------------------------------------------------------------
# Default order types — calibrated from the Value Stream Analysis workbook
# (Sales_Order_Data: 228,396 orders across a 44-day span).
#
#   daily_volume        = distinct orders of that type ÷ 44 days
#   avg_units_per_order = total inventory qty ÷ distinct orders
#   storage_split       = Paper Goods (Toilet+Towels) qty → 600, remainder → 400
#   kitting_split       = "Kitted …" category qty → kitting, remainder → packout
#   customer_split      = 300 vs 200 assumption — the sales data has no customer
#                         dimension, so a nominal 60/40 is kept for every type.
# ---------------------------------------------------------------------------
DEFAULT_ORDER_TYPES: List[OrderType] = [
    OrderType(
        id="SO", name="SO – Sales Order",
        daily_volume=3321, avg_units_per_order=11,
        storage_split=StorageSplit(paper_pct=37.8, consumable_pct=62.2),
        customer_split=CustomerSplit(cust1_pct=60.0, cust2_pct=40.0),
        kitting_split=KittingSplit(packout_pct=84.2, kitting_pct=15.8),
    ),
    OrderType(
        id="BE", name="BE – Bulk orders",
        daily_volume=1626, avg_units_per_order=6,
        storage_split=StorageSplit(paper_pct=30.2, consumable_pct=69.8),
        customer_split=CustomerSplit(cust1_pct=60.0, cust2_pct=40.0),
        kitting_split=KittingSplit(packout_pct=77.0, kitting_pct=23.0),
    ),
    OrderType(
        id="BW", name="BW – Bulk Warehouse",
        daily_volume=6, avg_units_per_order=3,
        storage_split=StorageSplit(paper_pct=3.7, consumable_pct=96.3),
        customer_split=CustomerSplit(cust1_pct=60.0, cust2_pct=40.0),
        kitting_split=KittingSplit(packout_pct=3.7, kitting_pct=96.3),
    ),
]
