"""
Warehouse Capacity Planner - Configuration & Data Models

BOM split logic (two independent splits):
  Split 1 — Storage split:    paper_pct + consumable_pct = 100%
                               How much of an order comes from 600 vs 400
  Split 2 — Customer split:   cust1_pct + cust2_pct = 100%
                               How 400 material divides between zone 300 and 200

Flow rules:
  600 → Smart Bulk (staging) → direct to 100 Final/Packout
  400 → 300 or 200 (by customer split %)
  300/200 → Kitting (kitting_pct) → back to 300/200 → 100 Final
  300/200 → direct to 100 Final (packout_pct)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


ZONE_NAMES: Dict[str, str] = {
    "600":        "Paper",
    "400":        "Consumables",
    "300":        "Customer Specific 1",
    "200":        "Customer Specific 2",
    "100":        "Final (ready to ship)",
    "SMART_BULK": "Smart Bulk (Paper staging)",
}

ZONE_FLOW_ORDER = ["600", "SMART_BULK", "400", "300", "200", "100"]


@dataclass
class StorageArea:
    id: str
    name: str
    zone: str
    volume_cuft: float
    avg_box_size_cuft: float
    efficiency: float
    units_per_box: float
    is_staging: bool = False
    max_concurrent_boxes: Optional[int] = None  # hard cap; None = no cap, use volume

    @property
    def capacity_boxes(self) -> int:
        """
        Effective capacity in boxes.
        If max_concurrent_boxes is set it acts as a hard cap —
        utilization is measured against it rather than volume.
        """
        vol_cap = int((self.volume_cuft * self.efficiency) / self.avg_box_size_cuft)
        if self.max_concurrent_boxes is not None:
            return min(self.max_concurrent_boxes, vol_cap)
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
class StorageSplit:
    """
    Split 1 — how an order's total units split between raw storage zones.
    paper_pct + consumable_pct should = 100.
    """
    paper_pct:      float = 50.0   # % going to 600 (Paper)
    consumable_pct: float = 50.0   # % going to 400 (Consumables)


@dataclass
class CustomerSplit:
    """
    Split 2 — how the 400 (Consumables) portion splits between customers.
    cust1_pct + cust2_pct should = 100.
    Applied only to the consumable_pct portion of the order.
    """
    cust1_pct: float = 60.0   # % of consumables going to 300 (Cust. Spec 1)
    cust2_pct: float = 40.0   # % of consumables going to 200 (Cust. Spec 2)


@dataclass
class KittingSplit:
    """
    What % of 300/200 material goes to Kitting vs direct to 100 Final.
    packout_pct + kitting_pct should = 100.
    Kitting material returns to 300/200 then flows to 100 normally.
    """
    packout_pct: float = 70.0
    kitting_pct: float = 30.0


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
        """Units drawn from zone 600 (Paper)."""
        return self.total_units(multiplier) * (self.storage_split.paper_pct / 100)

    def units_consumable(self, multiplier: float = 1.0) -> float:
        """Units drawn from zone 400 (Consumables)."""
        return self.total_units(multiplier) * (self.storage_split.consumable_pct / 100)

    def units_cust1(self, multiplier: float = 1.0) -> float:
        """Units routed to zone 300 (from the consumable portion)."""
        return self.units_consumable(multiplier) * (self.customer_split.cust1_pct / 100)

    def units_cust2(self, multiplier: float = 1.0) -> float:
        """Units routed to zone 200 (from the consumable portion)."""
        return self.units_consumable(multiplier) * (self.customer_split.cust2_pct / 100)

    def boxes_in_area(self, area: "StorageArea", multiplier: float = 1.0) -> float:
        """Boxes this order places in a given area, using area's units_per_box."""
        if area.zone == "600":
            units = self.units_paper(multiplier)
        elif area.zone == "SMART_BULK":
            units = self.units_paper(multiplier)   # mirrors 600 volume
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
# Default areas
# ---------------------------------------------------------------------------
DEFAULT_AREAS: List[StorageArea] = [
    StorageArea(id="zone600",    name="600 – Paper",
                zone="600",        volume_cuft=18000, avg_box_size_cuft=5.0,
                efficiency=0.70,   units_per_box=24.0),
    StorageArea(id="smart_bulk", name="Smart Bulk (Paper staging)",
                zone="SMART_BULK", volume_cuft=8000,  avg_box_size_cuft=5.0,
                efficiency=0.80,   units_per_box=24.0, is_staging=True,
                max_concurrent_boxes=200),
    StorageArea(id="zone400",    name="400 – Consumables",
                zone="400",        volume_cuft=14000, avg_box_size_cuft=3.5,
                efficiency=0.75,   units_per_box=12.0),
    StorageArea(id="zone300",    name="300 – Customer Specific 1",
                zone="300",        volume_cuft=9000,  avg_box_size_cuft=2.5,
                efficiency=0.80,   units_per_box=8.0),
    StorageArea(id="zone200",    name="200 – Customer Specific 2",
                zone="200",        volume_cuft=9000,  avg_box_size_cuft=2.5,
                efficiency=0.80,   units_per_box=8.0),
    StorageArea(id="packout",    name="Packout (Final assembly)",
                zone="100",        volume_cuft=6000,  avg_box_size_cuft=1.5,
                efficiency=0.85,   units_per_box=6.0),
    StorageArea(id="kitting",    name="Kitting (Custom kit assembly)",
                zone="100",        volume_cuft=4000,  avg_box_size_cuft=1.5,
                efficiency=0.85,   units_per_box=6.0,
                max_concurrent_boxes=150),
]

# ---------------------------------------------------------------------------
# Default order types
# ---------------------------------------------------------------------------
DEFAULT_ORDER_TYPES: List[OrderType] = [
    OrderType(
        id="SO", name="SO – Standard Order",
        daily_volume=120, avg_units_per_order=50,
        storage_split=StorageSplit(paper_pct=40.0, consumable_pct=60.0),
        customer_split=CustomerSplit(cust1_pct=60.0, cust2_pct=40.0),
        kitting_split=KittingSplit(packout_pct=70.0, kitting_pct=30.0),
    ),
    OrderType(
        id="SW", name="SW – Special Warehouse",
        daily_volume=40, avg_units_per_order=35,
        storage_split=StorageSplit(paper_pct=20.0, consumable_pct=80.0),
        customer_split=CustomerSplit(cust1_pct=50.0, cust2_pct=50.0),
        kitting_split=KittingSplit(packout_pct=50.0, kitting_pct=50.0),
    ),
    OrderType(
        id="BW", name="BW – Bulk Warehouse",
        daily_volume=25, avg_units_per_order=120,
        storage_split=StorageSplit(paper_pct=60.0, consumable_pct=40.0),
        customer_split=CustomerSplit(cust1_pct=70.0, cust2_pct=30.0),
        kitting_split=KittingSplit(packout_pct=80.0, kitting_pct=20.0),
    ),
]
