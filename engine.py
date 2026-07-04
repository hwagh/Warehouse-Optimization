"""
Warehouse Capacity Engine (per-area draw model)

Load in each area is the sum over order types of
    daily_volume × multiplier × draw[area]
Utilisation, bottleneck sequence, growth table and per-order breakdowns all
derive from that single rule.
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Dict, List
import pandas as pd

from config import (
    StorageArea, OrderType, AREA_NAMES, AREA_ORDER,
    DEFAULT_AREAS, DEFAULT_ORDER_TYPES,
)


@dataclass
class AreaSnapshot:
    area: StorageArea
    load_boxes: float
    load_units: float
    capacity_boxes: int
    capacity_units: int
    utilization_pct: float
    status: str
    contributing_orders: Dict[str, float]


@dataclass
class WarehouseSnapshot:
    multiplier: float
    areas: List[AreaSnapshot]

    @property
    def bottlenecks(self):
        return [a for a in self.areas if a.utilization_pct >= 100]

    @property
    def warnings(self):
        return [a for a in self.areas if 85 <= a.utilization_pct < 100]

    @property
    def total_capacity_boxes(self):
        return sum(a.capacity_boxes for a in self.areas)

    @property
    def total_capacity_units(self):
        return sum(a.capacity_units for a in self.areas)

    @property
    def total_load_boxes(self):
        return sum(a.load_boxes for a in self.areas)

    @property
    def total_load_units(self):
        return sum(a.load_units for a in self.areas)

    @property
    def overall_utilization(self):
        cap = self.total_capacity_boxes
        return (self.total_load_boxes / cap * 100) if cap > 0 else 0.0


class WarehouseEngine:
    def __init__(self, areas=None, order_types=None):
        self.areas       = areas       if areas       is not None else list(DEFAULT_AREAS)
        self.order_types = order_types if order_types is not None else list(DEFAULT_ORDER_TYPES)

    # ── core load ────────────────────────────────────────────────────────────
    def calc_loads(self, multiplier: float = 1.0) -> Dict[str, Dict[str, float]]:
        loads: Dict[str, Dict[str, float]] = {a.id: {} for a in self.areas}
        for area in self.areas:
            for ot in self.order_types:
                boxes = ot.boxes_in_area(area, multiplier)
                if boxes > 0:
                    loads[area.id][ot.id] = loads[area.id].get(ot.id, 0.0) + boxes
        return loads

    # ── snapshot ─────────────────────────────────────────────────────────────
    def snapshot(self, multiplier: float = 1.0) -> WarehouseSnapshot:
        loads = self.calc_loads(multiplier)
        snaps = []
        for area in self.areas:
            area_loads  = loads[area.id]
            total_boxes = sum(area_loads.values())
            total_units = total_boxes * area.units_per_box
            snaps.append(AreaSnapshot(
                area=area,
                load_boxes=total_boxes, load_units=total_units,
                capacity_boxes=area.capacity_boxes, capacity_units=area.capacity_units,
                utilization_pct=area.utilization_pct(total_boxes),
                status=area.status(total_boxes),
                contributing_orders=area_loads,
            ))
        return WarehouseSnapshot(multiplier=multiplier, areas=snaps)

    # ── bottleneck detection ─────────────────────────────────────────────────
    def find_bottleneck_multiplier(self, area_id, threshold_pct=100.0, max_mult=20.0, resolution=0.1):
        m = 1.0
        while m <= max_mult:
            snap = self.snapshot(multiplier=m)
            a = next((x for x in snap.areas if x.area.id == area_id), None)
            if a and a.utilization_pct >= threshold_pct:
                return round(m, 2)
            m += resolution
        return None

    def bottleneck_sequence(self, threshold_pct=100.0, max_mult=20.0):
        results = []
        for area in self.areas:
            m = self.find_bottleneck_multiplier(area.id, threshold_pct, max_mult)
            if m is not None:
                results.append((m, area.name, "BOTTLENECK"))
        results.sort(key=lambda x: x[0])
        return results

    # ── growth table ─────────────────────────────────────────────────────────
    def growth_table(self, max_multiplier=10.0, steps=18):
        rows = []
        step = max_multiplier / steps
        m = 1.0
        while m <= max_multiplier + 1e-9:
            snap = self.snapshot(round(m, 2))
            for a in snap.areas:
                rows.append({
                    "multiplier":      round(m, 2),
                    "area":            a.area.name,
                    "units_per_box":   a.area.units_per_box,
                    "load_boxes":      round(a.load_boxes, 1),
                    "load_units":      round(a.load_units, 0),
                    "capacity_boxes":  a.capacity_boxes,
                    "capacity_units":  a.capacity_units,
                    "utilization_pct": round(a.utilization_pct, 1),
                    "status":          a.status,
                })
            m += step
        return pd.DataFrame(rows)

    def area_summary(self, multiplier=1.0):
        snap = self.snapshot(multiplier)
        rows = []
        for a in snap.areas:
            rows.append({
                "area":            a.area.name,
                "capacity_boxes":  a.capacity_boxes,
                "capacity_units":  a.capacity_units,
                "load_boxes":      round(a.load_boxes, 1),
                "load_units":      round(a.load_units, 0),
                "utilization_pct": round(a.utilization_pct, 1),
                "status":          a.status,
            })
        return pd.DataFrame(rows)

    # kept under the old name so existing UI imports keep working
    def zone_summary(self, multiplier=1.0):
        return self.area_summary(multiplier)

    def capacity_summary(self):
        return pd.DataFrame([{
            "area": a.name,
            "rack_L": a.rack_length_cuft, "rack_D": a.rack_depth_cuft,
            "rack_H": a.rack_height_cuft, "num_racks": a.num_racks,
            "volume_cuft": a.volume_cuft, "avg_box_size_cuft": a.avg_box_size_cuft,
            "efficiency": a.efficiency, "units_per_box": a.units_per_box,
            "capacity_boxes": a.capacity_boxes, "capacity_units": a.capacity_units,
        } for a in self.areas])

    def order_impact(self, order_id, max_multiplier=3.0, steps=10):
        base = next((o for o in self.order_types if o.id == order_id), None)
        if base is None:
            return pd.DataFrame()
        rows = []
        step = (max_multiplier - 1.0) / max(steps - 1, 1)
        m = 1.0
        while m <= max_multiplier + 1e-9:
            scaled = replace(base, daily_volume=int(round(base.daily_volume * m)))
            others = [o for o in self.order_types if o.id != order_id]
            temp = WarehouseEngine(areas=self.areas, order_types=others + [scaled])
            snap = temp.snapshot(multiplier=1.0)
            for a in snap.areas:
                from_order = a.contributing_orders.get(order_id, 0.0)
                if from_order <= 0:
                    continue
                rows.append({
                    "order_multiplier": round(m, 2),
                    "area":             a.area.name,
                    "load_from_order":  round(from_order, 1),
                    "total_load":       round(a.load_boxes, 1),
                    "capacity":         a.capacity_boxes,
                    "utilization_pct":  round(a.utilization_pct, 1),
                    "status":           a.status,
                })
            m += step
        return pd.DataFrame(rows)

    def order_bom_summary(self, multiplier=1.0):
        rows = []
        for ot in self.order_types:
            for area in self.areas:
                boxes = ot.boxes_in_area(area, multiplier)
                if boxes <= 0:
                    continue
                rows.append({
                    "order": ot.id, "order_name": ot.name,
                    "area": area.name,
                    "boxes": round(boxes, 1),
                    "units": round(boxes * area.units_per_box, 0),
                    "units_per_box": area.units_per_box,
                })
        return pd.DataFrame(rows)
