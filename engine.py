"""
Warehouse Capacity Engine

Flow rules:
  600 → 300 / 200 (paper portion, by customer split)
  400 → 300 / 200 (consumable portion, by customer split)
  300 / 200 → 100 Final (direct packout or kitting loop)
  Kitting: % of 300/200 goes to kitting then back to same zone → 100
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple
import pandas as pd

from config import (
    StorageArea, OrderType, ZONE_NAMES, ZONE_FLOW_ORDER,
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
class FlowSummary:
    paper_to_300: float
    paper_to_200: float
    consumables_to_300: float
    consumables_to_200: float
    to_packout: Dict[str, float]
    to_kitting: Dict[str, float]


@dataclass
class WarehouseSnapshot:
    multiplier: float
    areas: List[AreaSnapshot]
    flow: FlowSummary

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
        self.areas       = areas       or list(DEFAULT_AREAS)
        self.order_types = order_types or list(DEFAULT_ORDER_TYPES)

    def _areas_for_zone(self, zone: str) -> List[StorageArea]:
        return [a for a in self.areas if a.zone == zone]

    # ── core load calculation ─────────────────────────────────────────────────

    def calc_loads(self, multiplier: float = 1.0) -> Dict[str, Dict[str, float]]:
        loads: Dict[str, Dict[str, float]] = {a.id: {} for a in self.areas}

        for ot in self.order_types:
            # 600 Paper
            for area in self._areas_for_zone("600"):
                boxes = ot.boxes_in_area(area, multiplier)
                if boxes > 0:
                    loads[area.id][ot.id] = loads[area.id].get(ot.id, 0) + boxes

            # 400 Consumables
            for area in self._areas_for_zone("400"):
                boxes = ot.boxes_in_area(area, multiplier)
                if boxes > 0:
                    loads[area.id][ot.id] = loads[area.id].get(ot.id, 0) + boxes

            # 300 — includes kitting loop returning material back to 300
            for area in self._areas_for_zone("300"):
                boxes = ot.boxes_in_area(area, multiplier)
                if boxes > 0:
                    kit_mult = 1 + (ot.kitting_split.kitting_pct / 100)
                    loads[area.id][ot.id] = loads[area.id].get(ot.id, 0) + boxes * kit_mult
                    self._apply_to_packout(loads, ot, boxes)

            # 200 — includes kitting loop returning material back to 200
            for area in self._areas_for_zone("200"):
                boxes = ot.boxes_in_area(area, multiplier)
                if boxes > 0:
                    kit_mult = 1 + (ot.kitting_split.kitting_pct / 100)
                    loads[area.id][ot.id] = loads[area.id].get(ot.id, 0) + boxes * kit_mult
                    self._apply_to_packout(loads, ot, boxes)

        return loads

    def _apply_to_packout(self, loads, ot: OrderType, boxes: float):
        direct = boxes * (ot.kitting_split.packout_pct / 100)
        if direct > 0:
            for area in self._areas_for_zone("100"):
                loads[area.id][ot.id] = loads[area.id].get(ot.id, 0) + direct

    # ── flow summary ──────────────────────────────────────────────────────────

    def calc_flow(self, multiplier: float = 1.0) -> FlowSummary:
        def zone_boxes(zone):
            ref = next((a for a in self.areas if a.zone == zone), None)
            if not ref: return 0.0
            return sum(ot.boxes_in_area(ref, multiplier) for ot in self.order_types)

        p300 = sum(
            ot.units_paper(multiplier) * (ot.customer_split.cust1_pct / 100)
            / (next((a for a in self.areas if a.zone == "300"), None) or type("", (), {"units_per_box": 1})()).units_per_box
            for ot in self.order_types
        )
        p200 = sum(
            ot.units_paper(multiplier) * (ot.customer_split.cust2_pct / 100)
            / (next((a for a in self.areas if a.zone == "200"), None) or type("", (), {"units_per_box": 1})()).units_per_box
            for ot in self.order_types
        )
        c300 = sum(ot.units_cust1(multiplier) / a.units_per_box
                   for ot in self.order_types
                   for a in self._areas_for_zone("300") if a.units_per_box > 0)
        c200 = sum(ot.units_cust2(multiplier) / a.units_per_box
                   for ot in self.order_types
                   for a in self._areas_for_zone("200") if a.units_per_box > 0)

        to_packout, to_kitting = {}, {}
        for ot in self.order_types:
            boxes_cs = sum(
                ot.boxes_in_area(a, multiplier)
                for a in self._areas_for_zone("300") + self._areas_for_zone("200")
            )
            to_packout[ot.id] = boxes_cs * (ot.kitting_split.packout_pct / 100)
            to_kitting[ot.id] = boxes_cs * (ot.kitting_split.kitting_pct / 100)

        return FlowSummary(
            paper_to_300=p300, paper_to_200=p200,
            consumables_to_300=c300, consumables_to_200=c200,
            to_packout=to_packout, to_kitting=to_kitting,
        )

    # ── snapshot ──────────────────────────────────────────────────────────────

    def snapshot(self, multiplier: float = 1.0) -> WarehouseSnapshot:
        loads = self.calc_loads(multiplier)
        flow  = self.calc_flow(multiplier)
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
        return WarehouseSnapshot(multiplier=multiplier, areas=snaps, flow=flow)

    # ── bottleneck detection ──────────────────────────────────────────────────

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

    # ── growth table ──────────────────────────────────────────────────────────

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
                    "zone":            a.area.zone,
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

    def zone_summary(self, multiplier=1.0):
        snap = self.snapshot(multiplier)
        rows = []
        for zone in ZONE_FLOW_ORDER:
            z_areas = [a for a in snap.areas if a.area.zone == zone]
            if not z_areas: continue
            cap_b  = sum(a.capacity_boxes for a in z_areas)
            cap_u  = sum(a.capacity_units  for a in z_areas)
            load_b = sum(a.load_boxes      for a in z_areas)
            load_u = sum(a.load_units      for a in z_areas)
            util   = (load_b / cap_b * 100) if cap_b > 0 else 0.0
            rows.append({
                "zone_code":       zone,
                "zone_name":       ZONE_NAMES.get(zone, zone),
                "areas":           ", ".join(a.area.name for a in z_areas),
                "capacity_boxes":  cap_b, "capacity_units":  cap_u,
                "load_boxes":      round(load_b, 1), "load_units": round(load_u, 0),
                "utilization_pct": round(util, 1),
            })
        return pd.DataFrame(rows)

    def capacity_summary(self):
        return pd.DataFrame([{
            "area": a.name, "zone": a.zone,
            "rack_L": a.rack_length_cuft, "rack_D": a.rack_depth_cuft,
            "rack_H": a.rack_height_cuft, "num_racks": a.num_racks,
            "volume_cuft": a.volume_cuft,
            "avg_box_size_cuft": a.avg_box_size_cuft,
            "efficiency": a.efficiency, "units_per_box": a.units_per_box,
            "capacity_boxes": a.capacity_boxes, "capacity_units": a.capacity_units,
        } for a in self.areas])

    def order_impact(self, order_id, max_multiplier=3.0, steps=10):
        """
        Scale ONLY the given order type from x1..max_multiplier (others held at
        x1.0) and report that order's box contribution to each area it touches,
        alongside the area's resulting total load, capacity, and utilization.
        """
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
            total_u = ot.total_units(multiplier)
            for zone, units in [
                ("600", ot.units_paper(multiplier)),
                ("400", ot.units_consumable(multiplier)),
                ("300", ot.units_cust1(multiplier)),
                ("200", ot.units_cust2(multiplier)),
            ]:
                if units <= 0: continue
                ref = next((a for a in self.areas if a.zone == zone), None)
                if not ref: continue
                rows.append({
                    "order": ot.id, "order_name": ot.name,
                    "zone": zone, "zone_name": ZONE_NAMES.get(zone, zone),
                    "units": round(units, 0),
                    "units_per_box": ref.units_per_box,
                    "boxes": round(units / ref.units_per_box, 1),
                    "pct_of_total": round(units / total_u * 100, 1) if total_u else 0,
                })
        return pd.DataFrame(rows)
