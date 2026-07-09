"""
Warehouse Capacity Planner — Desktop UI  [DEPRECATED]

This customtkinter desktop front-end is no longer maintained. The project's
single supported interface is the Streamlit web app (streamlit_app.py), which
runs on the current area/draw model (five areas: Dock, Back wall, Bulk, Mez,
Packouts; per-order-type per-area draws).

This file still targets the old zone/split model and imports names that no
longer exist in config.py (ZoneBOMSplit, DestinationSplit, ZONE_NAMES,
ZONE_FLOW_ORDER), so it will not run. It is kept only for historical
reference. Do not deploy or depend on it.

Run the app instead with:  streamlit run streamlit_app.py
"""

import sys as _sys

print(
    "\n[DEPRECATED] app.py (desktop UI) is no longer supported.\n"
    "It targets the old zone/split model and will not run against the\n"
    "current config.py. Use the Streamlit web app instead:\n\n"
    "    streamlit run streamlit_app.py\n"
)
_sys.exit(0)

# ---------------------------------------------------------------------------
# Legacy code below is preserved for reference only and is not executed.
# ---------------------------------------------------------------------------
"""
Warehouse Capacity Planner — Desktop UI
Run: python app.py
Requires: pip install customtkinter pandas
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import pandas as pd
import copy, sys, os

sys.path.insert(0, os.path.dirname(__file__))

from config import (
    StorageArea, OrderType, ZoneBOMSplit, DestinationSplit,
    ZONE_NAMES, ZONE_FLOW_ORDER,
    DEFAULT_AREAS, DEFAULT_ORDER_TYPES,
)
from engine import WarehouseEngine, WarehouseSnapshot

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── palette ───────────────────────────────────────────────────────────────────
C = {
    "bg":       "#0f1117",
    "surface":  "#1a1d27",
    "card":     "#20243a",
    "card2":    "#252944",
    "border":   "#2e3250",
    "accent":   "#4f6ef7",
    "accent2":  "#7c5cfc",
    "ok":       "#22c55e",
    "warn":     "#f59e0b",
    "critical": "#ef4444",
    "text":     "#e8eaf6",
    "muted":    "#6b7280",
    "hdr":      "#141728",
    "staging":  "#7c5cfc",
}

FONT_BODY  = ("Segoe UI", 13)
FONT_LBL   = ("Segoe UI", 11)
FONT_BOLD  = ("Segoe UI", 13, "bold")
FONT_H2    = ("Segoe UI", 15, "bold")
FONT_MONO  = ("Consolas", 12)
FONT_BIG   = ("Segoe UI", 26, "bold")
FONT_TINY  = ("Segoe UI", 10)

ZONE_COLORS = {
    "600":        "#4f6ef7",
    "SMART_BULK": "#7c5cfc",
    "400":        "#06b6d4",
    "300":        "#10b981",
    "200":        "#f59e0b",
    "100":        "#ef4444",
}


def status_color(pct: float) -> str:
    if pct >= 100: return C["critical"]
    if pct >= 85:  return C["warn"]
    if pct >= 70:  return "#f97316"
    return C["ok"]


def make_bar(parent, pct: float, width: int = 260, height: int = 10) -> ctk.CTkFrame:
    c = ctk.CTkFrame(parent, width=width, height=height,
                     fg_color=C["border"], corner_radius=5)
    c.pack_propagate(False)
    fw = int(min(pct / 100, 1.0) * width)
    if fw > 0:
        ctk.CTkFrame(c, width=fw, height=height,
                     fg_color=status_color(pct), corner_radius=5).place(x=0, y=0)
    return c


def section_label(parent, text: str):
    ctk.CTkLabel(parent, text=text, font=FONT_H2,
                 text_color=C["text"]).pack(anchor="w", padx=24, pady=(22, 4))


def muted_label(parent, text: str, wrap: int = 800):
    ctk.CTkLabel(parent, text=text, font=FONT_LBL,
                 text_color=C["muted"], wraplength=wrap).pack(
        anchor="w", padx=24, pady=(0, 12))


# ═══════════════════════════════════════════════════════════════════════════════
#  SETTINGS PAGE
# ═══════════════════════════════════════════════════════════════════════════════

class SettingsPage(ctk.CTkFrame):
    def __init__(self, parent, areas, order_types, on_save):
        super().__init__(parent, fg_color=C["bg"])
        self.areas       = areas
        self.order_types = order_types
        self.on_save     = on_save
        self._area_vars  = []
        self._order_vars = []
        self._build()

    def _build(self):
        # header
        hdr = ctk.CTkFrame(self, fg_color=C["hdr"], height=56, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="Settings", font=FONT_H2,
                     text_color=C["text"]).pack(side="left", padx=20, pady=12)
        ctk.CTkButton(hdr, text="Save & recalculate", font=FONT_BOLD,
                      fg_color=C["accent"], hover_color=C["accent2"],
                      command=self._save, height=34, width=190).pack(
            side="right", padx=20, pady=10)

        scroll = ctk.CTkScrollableFrame(self, fg_color=C["bg"])
        scroll.pack(fill="both", expand=True)

        self._build_areas(scroll)
        self._build_orders(scroll)
        ctk.CTkFrame(scroll, fg_color="transparent", height=30).pack()

    # ── Area config ──────────────────────────────────────────────────────────

    def _build_areas(self, parent):
        section_label(parent, "Storage areas")
        muted_label(parent, "Physical dimensions and efficiency for each storage area.")

        self._area_vars = []
        for area in self.areas:
            zc = ZONE_COLORS.get(area.zone, C["accent"])
            card = ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=12)
            card.pack(fill="x", padx=20, pady=5)

            # title bar with zone colour strip
            hdr = ctk.CTkFrame(card, fg_color=C["card2"], corner_radius=8)
            hdr.pack(fill="x", padx=10, pady=(10, 6))
            ctk.CTkFrame(hdr, fg_color=zc, width=4, corner_radius=2).pack(
                side="left", fill="y", padx=(8, 10), pady=6)
            ctk.CTkLabel(hdr, text=area.name, font=FONT_BOLD,
                         text_color=C["text"]).pack(side="left", pady=10)
            zone_label = "Staging" if area.is_staging else f"Zone {area.zone}"
            ctk.CTkLabel(hdr, text=zone_label, font=FONT_LBL,
                         text_color=zc).pack(side="right", padx=14)

            # fields
            fr = ctk.CTkFrame(card, fg_color="transparent")
            fr.pack(fill="x", padx=14, pady=(0, 12))

            v_vol = tk.StringVar(value=str(area.volume_cuft))
            v_box = tk.StringVar(value=str(area.avg_box_size_cuft))
            v_eff = tk.StringVar(value=str(area.efficiency))
            v_upb = tk.StringVar(value=str(area.units_per_box))
            self._area_vars.append((area.id, v_vol, v_box, v_eff, v_upb))

            for lbl, var, tip in [
                ("Volume (cu ft)",        v_vol, "Total cubic footage"),
                ("Avg box size (cu ft)",  v_box, "Average box cubic feet"),
                ("Efficiency (0–1)",      v_eff, "Usable fraction after aisles"),
                ("Units per box",         v_upb, "Avg units that fit in one box here"),
            ]:
                col = ctk.CTkFrame(fr, fg_color="transparent")
                col.pack(side="left", padx=(0, 18))
                ctk.CTkLabel(col, text=lbl, font=FONT_LBL,
                             text_color=C["muted"]).pack(anchor="w")
                ctk.CTkEntry(col, textvariable=var, width=145, font=FONT_BODY,
                             fg_color=C["surface"],
                             border_color=C["border"]).pack(anchor="w", pady=(2, 0))
                ctk.CTkLabel(col, text=tip, font=FONT_TINY,
                             text_color=C["muted"]).pack(anchor="w")

            # computed capacity (boxes + units)
            cap_lbl = ctk.CTkLabel(card,
                text=f"Capacity: {area.capacity_boxes:,} boxes  ·  {area.capacity_units:,} units  ·  {area.units_per_box:.0f} units/box",
                font=FONT_LBL, text_color=zc)
            cap_lbl.pack(anchor="w", padx=14, pady=(0, 10))

    # ── Order type config ────────────────────────────────────────────────────

    def _build_orders(self, parent):
        section_label(parent, "Order types — BOM & routing")
        muted_label(parent,
            "Set daily volume, average units per order, and the % of units drawn from "
            "each zone (BOM split). Packout vs Kitting split controls where 300/200 "
            "material goes after pick.")

        self._order_vars = []
        for ot in self.order_types:
            card = ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=12)
            card.pack(fill="x", padx=20, pady=8)

            # title
            hdr = ctk.CTkFrame(card, fg_color=C["card2"], corner_radius=8)
            hdr.pack(fill="x", padx=10, pady=(10, 8))
            ctk.CTkLabel(hdr, text=ot.name, font=FONT_BOLD,
                         text_color=C["text"]).pack(side="left", padx=14, pady=10)

            # base fields
            br = ctk.CTkFrame(card, fg_color="transparent")
            br.pack(fill="x", padx=14, pady=(0, 8))

            v_vol  = tk.StringVar(value=str(ot.daily_volume))
            v_qty  = tk.StringVar(value=str(ot.avg_units_per_order))

            for lbl, var in [("Daily volume (orders)", v_vol),
                              ("Avg units / order",     v_qty)]:
                col = ctk.CTkFrame(br, fg_color="transparent")
                col.pack(side="left", padx=(0, 20))
                ctk.CTkLabel(col, text=lbl, font=FONT_LBL,
                             text_color=C["muted"]).pack(anchor="w")
                ctk.CTkEntry(col, textvariable=var, width=160, font=FONT_BODY,
                             fg_color=C["surface"],
                             border_color=C["border"]).pack(anchor="w", pady=(2, 0))

            # ── BOM zone splits ──────────────────────────────────────────
            ctk.CTkLabel(card, text="BOM zone split (must sum to 100%)",
                         font=FONT_LBL, text_color=C["muted"]).pack(
                anchor="w", padx=14, pady=(4, 4))

            bom_row = ctk.CTkFrame(card, fg_color="transparent")
            bom_row.pack(fill="x", padx=14, pady=(0, 8))

            bom_vars = {}
            for zone, lbl in [("600","600 – Paper %"), ("400","400 – Consumables %"),
                               ("300","300 – Cust.Spec 1 %"), ("200","200 – Cust.Spec 2 %")]:
                col = ctk.CTkFrame(bom_row, fg_color="transparent")
                col.pack(side="left", padx=(0, 14))
                zc = ZONE_COLORS.get(zone, C["accent"])
                ctk.CTkLabel(col, text=lbl, font=FONT_LBL,
                             text_color=zc).pack(anchor="w")
                v = tk.StringVar(value=str(getattr(ot.bom_split, f"zone_{zone}", 0.0)))
                ctk.CTkEntry(col, textvariable=v, width=90, font=FONT_BODY,
                             fg_color=C["surface"],
                             border_color=C["border"]).pack(anchor="w", pady=(2, 0))
                bom_vars[zone] = v

            # ── Destination split ────────────────────────────────────────
            ctk.CTkLabel(card, text="300/200 destination split",
                         font=FONT_LBL, text_color=C["muted"]).pack(
                anchor="w", padx=14, pady=(4, 4))

            ds_row = ctk.CTkFrame(card, fg_color="transparent")
            ds_row.pack(fill="x", padx=14, pady=(0, 12))

            v_pack = tk.StringVar(value=str(ot.destination_split.packout_pct))
            v_kit  = tk.StringVar(value=str(ot.destination_split.kitting_pct))

            for lbl, var, tip in [
                ("→ Packout %", v_pack, "Final assembly"),
                ("→ Kitting %", v_kit,  "Custom kit assembly"),
            ]:
                col = ctk.CTkFrame(ds_row, fg_color="transparent")
                col.pack(side="left", padx=(0, 18))
                ctk.CTkLabel(col, text=lbl, font=FONT_LBL,
                             text_color=C["muted"]).pack(anchor="w")
                ctk.CTkEntry(col, textvariable=var, width=100, font=FONT_BODY,
                             fg_color=C["surface"],
                             border_color=C["border"]).pack(anchor="w", pady=(2, 0))
                ctk.CTkLabel(col, text=tip, font=FONT_TINY,
                             text_color=C["muted"]).pack(anchor="w")

            self._order_vars.append((ot.id, v_vol, v_qty, bom_vars, v_pack, v_kit))

    # ── Save ─────────────────────────────────────────────────────────────────

    def _save(self):
        try:
            area_map = {a.id: a for a in self.areas}
            for (aid, v_vol, v_box, v_eff, v_upb) in self._area_vars:
                a = area_map[aid]
                a.volume_cuft       = float(v_vol.get())
                a.avg_box_size_cuft = float(v_box.get())
                a.efficiency        = float(v_eff.get())
                a.units_per_box     = float(v_upb.get())

            ot_map = {o.id: o for o in self.order_types}
            for (oid, v_vol, v_qty, bom_vars, v_pack, v_kit) in self._order_vars:
                ot = ot_map[oid]
                ot.daily_volume        = int(v_vol.get())
                ot.avg_units_per_order = int(v_qty.get())
                ot.bom_split = ZoneBOMSplit(
                    zone_600=float(bom_vars["600"].get()),
                    zone_400=float(bom_vars["400"].get()),
                    zone_300=float(bom_vars["300"].get()),
                    zone_200=float(bom_vars["200"].get()),
                )
                ot.destination_split = DestinationSplit(
                    packout_pct=float(v_pack.get()),
                    kitting_pct=float(v_kit.get()),
                )

            self.on_save()
            messagebox.showinfo("Saved", "Settings saved — analysis updated.")
        except ValueError as e:
            messagebox.showerror("Invalid input", f"Check your values:\n{e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  ANALYSIS PAGE
# ═══════════════════════════════════════════════════════════════════════════════

class AnalysisPage(ctk.CTkFrame):
    def __init__(self, parent, engine_ref):
        super().__init__(parent, fg_color=C["bg"])
        self.engine_ref = engine_ref
        self._mult      = 1.0
        self._build()

    def _build(self):
        # header
        hdr = ctk.CTkFrame(self, fg_color=C["hdr"], height=56, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="Analysis", font=FONT_H2,
                     text_color=C["text"]).pack(side="left", padx=20, pady=12)

        ctrl = ctk.CTkFrame(hdr, fg_color="transparent")
        ctrl.pack(side="right", padx=20, pady=8)
        ctk.CTkLabel(ctrl, text="Volume multiplier", font=FONT_LBL,
                     text_color=C["muted"]).pack(side="left", padx=(0, 8))
        self._mult_lbl = ctk.CTkLabel(ctrl, text="×1.0", font=FONT_BOLD,
                                       text_color=C["accent"], width=48)
        self._mult_lbl.pack(side="right", padx=(6, 0))
        ctk.CTkSlider(ctrl, from_=1.0, to=10.0, number_of_steps=90,
                      width=240, command=self._on_slider,
                      button_color=C["accent"],
                      button_hover_color=C["accent2"],
                      progress_color=C["accent"]).pack(side="left")

        # tabs
        tabbar = ctk.CTkFrame(self, fg_color=C["surface"], height=44, corner_radius=0)
        tabbar.pack(fill="x")
        self._tab_btns  = {}
        self._active_tab = tk.StringVar(value="overview")
        for tid, lbl in [("overview","Overview"), ("flow","Material flow"),
                          ("areas","Area detail"), ("growth","Growth table"),
                          ("bottleneck","Bottlenecks"), ("bom","BOM breakdown")]:
            b = ctk.CTkButton(tabbar, text=lbl, font=FONT_BODY,
                               fg_color="transparent", hover_color=C["card"],
                               text_color=C["muted"], height=44, corner_radius=0,
                               command=lambda t=tid: self._switch_tab(t))
            b.pack(side="left")
            self._tab_btns[tid] = b

        self._content = ctk.CTkFrame(self, fg_color=C["bg"])
        self._content.pack(fill="both", expand=True)
        self._switch_tab("overview")

    def _on_slider(self, val):
        self._mult = round(float(val), 1)
        self._mult_lbl.configure(text=f"×{self._mult:.1f}")
        self._refresh()

    def _switch_tab(self, tab_id):
        self._active_tab.set(tab_id)
        for tid, btn in self._tab_btns.items():
            btn.configure(
                text_color=C["text"] if tid == tab_id else C["muted"],
                fg_color=C["card"]   if tid == tab_id else "transparent",
            )
        self._refresh()

    def refresh(self):
        self._refresh()

    def _refresh(self):
        for w in self._content.winfo_children():
            w.destroy()
        tab    = self._active_tab.get()
        engine = self.engine_ref()
        snap   = engine.snapshot(multiplier=self._mult)

        if   tab == "overview":    self._tab_overview(snap, engine)
        elif tab == "flow":        self._tab_flow(snap, engine)
        elif tab == "areas":       self._tab_areas(snap)
        elif tab == "growth":      self._tab_growth(engine)
        elif tab == "bottleneck":  self._tab_bottleneck(engine)
        elif tab == "bom":         self._tab_bom(engine)

    # ── Overview ─────────────────────────────────────────────────────────────

    def _tab_overview(self, snap: WarehouseSnapshot, engine):
        scroll = ctk.CTkScrollableFrame(self._content, fg_color=C["bg"])
        scroll.pack(fill="both", expand=True)

        # KPIs
        kpi = ctk.CTkFrame(scroll, fg_color="transparent")
        kpi.pack(fill="x", padx=20, pady=(20, 8))
        for val, lbl, col in [
            (f"{snap.total_capacity_boxes:,}",    "Total capacity (boxes)", C["text"]),
            (f"{snap.total_capacity_units:,}",    "Total capacity (units)", C["text"]),
            (f"{snap.total_load_boxes:,.0f}",     "Current load (boxes)",   C["text"]),
            (f"{snap.overall_utilization:.1f}%",  "Overall utilization",    status_color(snap.overall_utilization)),
            (str(len(snap.bottlenecks)),           "Over capacity",          C["critical"] if snap.bottlenecks else C["ok"]),
            (str(len(snap.warnings)),              "Near limit (85%+)",      C["warn"] if snap.warnings else C["ok"]),
        ]:
            c = ctk.CTkFrame(kpi, fg_color=C["card"], corner_radius=12)
            c.pack(side="left", padx=(0, 10), ipadx=14, ipady=8)
            ctk.CTkLabel(c, text=val, font=FONT_BIG, text_color=col).pack(pady=(10, 2))
            ctk.CTkLabel(c, text=lbl, font=FONT_LBL, text_color=C["muted"]).pack(pady=(0, 10))

        # alerts
        for a in snap.bottlenecks:
            self._alert(scroll, f"BOTTLENECK — {a.area.name} is at {a.utilization_pct:.1f}%", C["critical"])
        for a in snap.warnings:
            self._alert(scroll, f"WARNING — {a.area.name} is at {a.utilization_pct:.1f}%", C["warn"])
        if not snap.bottlenecks and not snap.warnings:
            self._alert(scroll, f"All areas within safe capacity at ×{self._mult:.1f}", C["ok"])

        # area bars
        section_label(scroll, "Area utilization")
        for a in snap.areas:
            zc = ZONE_COLORS.get(a.area.zone, C["accent"])
            row = ctk.CTkFrame(scroll, fg_color=C["card"], corner_radius=10)
            row.pack(fill="x", padx=20, pady=4)

            # colour zone strip
            ctk.CTkFrame(row, fg_color=zc, width=4, corner_radius=2).pack(
                side="left", fill="y", padx=(8, 10), pady=8)

            left = ctk.CTkFrame(row, fg_color="transparent", width=200)
            left.pack(side="left", pady=12)
            left.pack_propagate(False)
            ctk.CTkLabel(left, text=a.area.name, font=FONT_BOLD,
                         text_color=C["text"]).pack(anchor="w")
            zone_str = "Staging" if a.area.is_staging else f"Zone {a.area.zone}"
            ctk.CTkLabel(left, text=zone_str, font=FONT_LBL,
                         text_color=zc).pack(anchor="w")

            mid = ctk.CTkFrame(row, fg_color="transparent")
            mid.pack(side="left", fill="x", expand=True, padx=8, pady=12)
            make_bar(mid, a.utilization_pct, width=320).pack(anchor="w", pady=(4, 2))
            ctk.CTkLabel(mid,
                text=f"{a.load_boxes:,.0f} / {a.capacity_boxes:,} boxes  ·  "
                     f"{a.load_units:,.0f} / {a.capacity_units:,} units  ·  "
                     f"{a.area.units_per_box:.0f} units/box",
                font=FONT_LBL, text_color=C["muted"]).pack(anchor="w")

            right = ctk.CTkFrame(row, fg_color="transparent", width=130)
            right.pack(side="right", padx=14, pady=12)
            right.pack_propagate(False)
            ctk.CTkLabel(right, text=f"{a.utilization_pct:.1f}%", font=FONT_BOLD,
                         text_color=status_color(a.utilization_pct)).pack(anchor="e")
            ctk.CTkLabel(right, text=a.status, font=FONT_LBL,
                         text_color=status_color(a.utilization_pct)).pack(anchor="e")

    # ── Material flow ─────────────────────────────────────────────────────────

    def _tab_flow(self, snap: WarehouseSnapshot, engine):
        scroll = ctk.CTkScrollableFrame(self._content, fg_color=C["bg"])
        scroll.pack(fill="both", expand=True)
        section_label(scroll, "Material flow summary")
        muted_label(scroll,
            f"Movement volumes at ×{self._mult:.1f} — showing how material flows "
            "through each zone based on order BOM splits and movement rules.")

        f = snap.flow

        # flow cards
        flows = [
            ("600 – Paper", "→ Smart Bulk (staging)", f"{f.paper_to_smart_bulk:.0f} boxes/day",
             "600", "SMART_BULK",
             "Paper pallets pulled from 600 and staged in Smart Bulk for small-box picking"),
            ("400 – Consumables", "→ 300 / 200", f"{f.consumables_to_300:.0f} / {f.consumables_to_200:.0f} boxes/day",
             "400", "300",
             "Consumables routed to Customer Specific 1 (300) or 2 (200) per customer need"),
            ("300 + 200", "→ Packout / Kitting", "", "300", "100",
             "Customer-specific material routed to final assembly (Packout) or custom kit build (Kitting)"),
        ]

        for from_lbl, to_lbl, vol, from_zone, to_zone, rule in flows:
            card = ctk.CTkFrame(scroll, fg_color=C["card"], corner_radius=12)
            card.pack(fill="x", padx=20, pady=8)
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=14)

            # from
            fc = ctk.CTkFrame(row, fg_color=C["card2"], corner_radius=8)
            fc.pack(side="left", ipadx=12, ipady=6)
            ctk.CTkLabel(fc, text=from_lbl, font=FONT_BOLD,
                         text_color=ZONE_COLORS.get(from_zone, C["accent"])).pack(pady=6, padx=12)

            ctk.CTkLabel(row, text="  →  ", font=FONT_H2,
                         text_color=C["muted"]).pack(side="left")

            # to
            tc = ctk.CTkFrame(row, fg_color=C["card2"], corner_radius=8)
            tc.pack(side="left", ipadx=12, ipady=6)
            ctk.CTkLabel(tc, text=to_lbl, font=FONT_BOLD,
                         text_color=ZONE_COLORS.get(to_zone, C["accent"])).pack(pady=6, padx=12)

            if vol:
                ctk.CTkLabel(row, text=f"  {vol}", font=FONT_BOLD,
                             text_color=C["text"]).pack(side="left", padx=12)

            ctk.CTkLabel(card, text=rule, font=FONT_LBL,
                         text_color=C["muted"]).pack(anchor="w", padx=16, pady=(0, 12))

        # Packout / Kitting breakdown per order type
        section_label(scroll, "Packout vs Kitting — by order type")
        muted_label(scroll, "Boxes of 300/200 material routed to each destination per day.")

        self._mini_table(scroll,
            headers=["Order", "→ Packout (boxes)", "→ Kitting (boxes)", "Packout %", "Kitting %"],
            rows=[
                [ot.id,
                 f"{f.to_packout.get(ot.id, 0):.1f}",
                 f"{f.to_kitting.get(ot.id, 0):.1f}",
                 f"{ot.destination_split.packout_pct:.0f}%",
                 f"{ot.destination_split.kitting_pct:.0f}%"]
                for ot in engine.order_types
            ])

    # ── Area detail ───────────────────────────────────────────────────────────

    def _tab_areas(self, snap: WarehouseSnapshot):
        scroll = ctk.CTkScrollableFrame(self._content, fg_color=C["bg"])
        scroll.pack(fill="both", expand=True)
        section_label(scroll, "Area detail — order contributions")
        muted_label(scroll, "Load breakdown per area showing contribution from each order type.")

        for a in snap.areas:
            zc = ZONE_COLORS.get(a.area.zone, C["accent"])
            card = ctk.CTkFrame(scroll, fg_color=C["card"], corner_radius=12)
            card.pack(fill="x", padx=20, pady=8)

            hdr = ctk.CTkFrame(card, fg_color=C["card2"], corner_radius=8)
            hdr.pack(fill="x", padx=10, pady=(10, 8))
            ctk.CTkFrame(hdr, fg_color=zc, width=4, corner_radius=2).pack(
                side="left", fill="y", padx=(8, 10), pady=6)
            ctk.CTkLabel(hdr, text=a.area.name, font=FONT_BOLD,
                         text_color=C["text"]).pack(side="left", pady=10)
            ctk.CTkLabel(hdr,
                text=f"{a.utilization_pct:.1f}%  ·  {a.load_boxes:,.0f} / {a.capacity_boxes:,} boxes",
                font=FONT_LBL, text_color=status_color(a.utilization_pct)).pack(side="right", padx=14)

            make_bar(card, a.utilization_pct, width=500).pack(anchor="w", padx=14, pady=(0, 10))

            if a.contributing_orders:
                self._mini_table(card,
                    headers=["Order", "Boxes", "Units", "% of area load"],
                    rows=[
                        [oid,
                         f"{boxes:,.1f}",
                         f"{boxes * a.area.units_per_box:,.0f}",
                         f"{boxes/a.load_boxes*100:.1f}%" if a.load_boxes else "—"]
                        for oid, boxes in sorted(
                            a.contributing_orders.items(), key=lambda x: x[1], reverse=True)
                    ])
            else:
                ctk.CTkLabel(card, text="No orders currently load this area.",
                             font=FONT_LBL, text_color=C["muted"]).pack(
                    anchor="w", padx=14, pady=(0, 12))

    # ── Growth table ──────────────────────────────────────────────────────────

    def _tab_growth(self, engine):
        scroll = ctk.CTkScrollableFrame(self._content, fg_color=C["bg"])
        scroll.pack(fill="both", expand=True)
        section_label(scroll, "Growth table")
        muted_label(scroll, "Utilization % per area as volume scales ×1 → ×10. Red = over capacity.")

        df = engine.growth_table(max_multiplier=10.0, steps=18)
        pivot = df.pivot_table(
            index="multiplier", columns="area",
            values="utilization_pct", aggfunc="first"
        ).reset_index()

        area_names = [a.name for a in engine.areas]
        rows = []
        for _, row in pivot.iterrows():
            cells = [f"×{row['multiplier']:.1f}"]
            for name in area_names:
                v = row.get(name, 0)
                cells.append(f"{v:.0f}%")
            rows.append(cells)

        self._color_table(scroll, ["Multiplier"] + area_names, rows)

    # ── Bottlenecks ───────────────────────────────────────────────────────────

    def _tab_bottleneck(self, engine):
        scroll = ctk.CTkScrollableFrame(self._content, fg_color=C["bg"])
        scroll.pack(fill="both", expand=True)
        section_label(scroll, "Bottleneck sequence")
        muted_label(scroll,
            "Order in which areas will hit capacity as volume grows. "
            "Smart Bulk tends to redline first because it is a compact staging area.")

        seq_100 = engine.bottleneck_sequence(threshold_pct=100.0, max_mult=20.0)
        seq_85  = engine.bottleneck_sequence(threshold_pct=85.0,  max_mult=20.0)

        if not seq_100:
            self._alert(scroll, "No areas hit 100% capacity within ×20 volume.", C["ok"])
        else:
            for rank, (mult, area_name, _) in enumerate(seq_100, 1):
                card = ctk.CTkFrame(scroll, fg_color=C["card"], corner_radius=12)
                card.pack(fill="x", padx=20, pady=6)
                row = ctk.CTkFrame(card, fg_color="transparent")
                row.pack(fill="x", padx=16, pady=14)
                rc = [C["critical"], C["warn"], "#f97316"][min(rank - 1, 2)]
                ctk.CTkLabel(row, text=f"#{rank}", font=FONT_BIG,
                             text_color=rc, width=52).pack(side="left")
                info = ctk.CTkFrame(row, fg_color="transparent")
                info.pack(side="left", padx=14)
                ctk.CTkLabel(info, text=area_name, font=FONT_BOLD,
                             text_color=C["text"]).pack(anchor="w")
                ctk.CTkLabel(info, text=f"Hits 100% capacity at ×{mult:.1f} volume",
                             font=FONT_LBL, text_color=C["muted"]).pack(anchor="w")
                if rank == 1:
                    ctk.CTkLabel(row, text="⚠ Address first", font=FONT_BOLD,
                                 text_color=C["critical"]).pack(side="right", padx=8)

        section_label(scroll, "Early warning — 85% threshold")
        if seq_85:
            self._mini_table(scroll,
                headers=["Area", "Reaches 85% at"],
                rows=[[name, f"×{mult:.1f}"] for mult, name, _ in seq_85])
        else:
            self._alert(scroll, "No areas reach 85% within ×20 volume.", C["ok"])

        ctk.CTkFrame(scroll, fg_color="transparent", height=20).pack()

    # ── BOM breakdown ─────────────────────────────────────────────────────────

    def _tab_bom(self, engine):
        scroll = ctk.CTkScrollableFrame(self._content, fg_color=C["bg"])
        scroll.pack(fill="both", expand=True)
        section_label(scroll, "BOM breakdown")
        muted_label(scroll,
            f"Units and box equivalents pulled from each zone per order type at ×{self._mult:.1f} volume.")

        df = engine.order_bom_summary(multiplier=self._mult)

        for ot in engine.order_types:
            ot_df = df[df["order"] == ot.id]
            card = ctk.CTkFrame(scroll, fg_color=C["card"], corner_radius=12)
            card.pack(fill="x", padx=20, pady=8)

            hdr = ctk.CTkFrame(card, fg_color=C["card2"], corner_radius=8)
            hdr.pack(fill="x", padx=10, pady=(10, 8))
            ctk.CTkLabel(hdr, text=ot.name, font=FONT_BOLD,
                         text_color=C["text"]).pack(side="left", padx=14, pady=10)
            total_units = ot.daily_volume * self._mult * ot.avg_units_per_order
            ctk.CTkLabel(hdr, text=f"{total_units:,.0f} total units/day at ×{self._mult:.1f}",
                         font=FONT_LBL, text_color=C["muted"]).pack(side="right", padx=14)

            if not ot_df.empty:
                # zone bars
                for _, row in ot_df.iterrows():
                    zc = ZONE_COLORS.get(row["zone"], C["accent"])
                    zrow = ctk.CTkFrame(card, fg_color="transparent")
                    zrow.pack(fill="x", padx=14, pady=3)
                    ctk.CTkLabel(zrow, text=row["zone_name"], font=FONT_LBL,
                                 text_color=zc, width=180).pack(side="left")
                    ctk.CTkLabel(zrow, text=f"{row['bom_pct']:.0f}%", font=FONT_BOLD,
                                 text_color=zc, width=44).pack(side="left")
                    make_bar(zrow, row["bom_pct"], width=200, height=8).pack(
                        side="left", padx=8)
                    ctk.CTkLabel(zrow, text=f"{row['units']:,.0f} units  ·  {row['boxes']:.1f} boxes",
                                 font=FONT_LBL, text_color=C["muted"]).pack(side="left", padx=8)

            # destination split
            dest_row = ctk.CTkFrame(card, fg_color="transparent")
            dest_row.pack(fill="x", padx=14, pady=(6, 12))
            ctk.CTkLabel(dest_row, text="300/200 goes to:", font=FONT_LBL,
                         text_color=C["muted"]).pack(side="left", padx=(0, 10))
            ctk.CTkLabel(dest_row,
                text=f"Packout {ot.destination_split.packout_pct:.0f}%  ·  Kitting {ot.destination_split.kitting_pct:.0f}%",
                font=FONT_BOLD, text_color=C["text"]).pack(side="left")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _alert(self, parent, text, color):
        f = ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=8,
                         border_color=color, border_width=2)
        f.pack(fill="x", padx=20, pady=4)
        ctk.CTkLabel(f, text=text, font=FONT_BODY, text_color=color).pack(
            anchor="w", padx=14, pady=8)

    def _mini_table(self, parent, headers, rows):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=16, pady=(4, 12))
        for ci, h in enumerate(headers):
            ctk.CTkLabel(frame, text=h, font=("Segoe UI", 11, "bold"),
                         text_color=C["muted"]).grid(
                row=0, column=ci, padx=(0, 22), pady=(0, 4), sticky="w")
        ctk.CTkFrame(frame, fg_color=C["border"], height=1).grid(
            row=1, column=0, columnspan=len(headers), sticky="ew", pady=(0, 4))
        for ri, row in enumerate(rows, 2):
            for ci, cell in enumerate(row):
                ctk.CTkLabel(frame, text=cell, font=FONT_MONO,
                             text_color=C["text"]).grid(
                    row=ri, column=ci, padx=(0, 22), pady=2, sticky="w")

    def _color_table(self, parent, headers, rows):
        frame = ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=12)
        frame.pack(fill="x", padx=20, pady=(0, 20))
        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(padx=16, pady=12)
        for ci, h in enumerate(headers):
            ctk.CTkLabel(inner, text=h, font=("Segoe UI", 11, "bold"),
                         text_color=C["muted"],
                         width=70 if ci else 80).grid(
                row=0, column=ci, padx=4, pady=(0, 6), sticky="w")
        for ri, row in enumerate(rows, 1):
            for ci, cell in enumerate(row):
                color = C["text"] if ci == 0 else status_color(float(cell.replace("%","")))
                ctk.CTkLabel(inner, text=cell, font=FONT_MONO,
                             text_color=color,
                             width=70 if ci else 80).grid(
                    row=ri, column=ci, padx=4, pady=2, sticky="w")


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN APP
# ═══════════════════════════════════════════════════════════════════════════════

class WarehouseApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Warehouse Capacity Planner")
        self.geometry("1340x860")
        self.minsize(1000, 660)
        self.configure(fg_color=C["bg"])

        self._areas       = [copy.deepcopy(a) for a in DEFAULT_AREAS]
        self._order_types = [copy.deepcopy(o) for o in DEFAULT_ORDER_TYPES]
        self._engine      = self._make_engine()
        self._build()

    def _make_engine(self):
        return WarehouseEngine(
            areas=[copy.deepcopy(a) for a in self._areas],
            order_types=[copy.deepcopy(o) for o in self._order_types],
        )

    def _build(self):
        # nav
        nav = ctk.CTkFrame(self, fg_color=C["surface"], width=210, corner_radius=0)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)

        ctk.CTkLabel(nav, text="⬡", font=("Segoe UI", 34),
                     text_color=C["accent"]).pack(pady=(28, 2))
        ctk.CTkLabel(nav, text="Warehouse\nPlanner", font=("Segoe UI", 13, "bold"),
                     text_color=C["text"], justify="center").pack(pady=(0, 28))

        self._nav_btns = {}
        for pid, icon, lbl in [("analysis","◈","Analysis"), ("settings","⚙","Settings")]:
            b = ctk.CTkButton(nav, text=f"  {icon}  {lbl}", font=FONT_BODY,
                               anchor="w", fg_color="transparent",
                               hover_color=C["card"], text_color=C["muted"],
                               height=44, corner_radius=8,
                               command=lambda p=pid: self._show(p))
            b.pack(fill="x", padx=10, pady=2)
            self._nav_btns[pid] = b

        # zone legend
        ctk.CTkFrame(nav, fg_color=C["border"], height=1).pack(fill="x", padx=14, pady=20)
        ctk.CTkLabel(nav, text="Zones", font=FONT_LBL,
                     text_color=C["muted"]).pack(anchor="w", padx=16, pady=(0, 6))
        for zone, name in [("600","Paper"), ("SMART_BULK","Smart Bulk"),
                            ("400","Consumables"), ("300","Cust. Spec 1"),
                            ("200","Cust. Spec 2"), ("100","Final")]:
            zr = ctk.CTkFrame(nav, fg_color="transparent")
            zr.pack(fill="x", padx=14, pady=1)
            ctk.CTkFrame(zr, fg_color=ZONE_COLORS.get(zone, C["accent"]),
                         width=8, height=8, corner_radius=2).pack(side="left", padx=(0, 6))
            ctk.CTkLabel(zr, text=f"{zone} – {name}", font=FONT_TINY,
                         text_color=C["muted"]).pack(side="left")

        ctk.CTkFrame(nav, fg_color=C["border"], height=1).pack(fill="x", padx=14, pady=16)
        ctk.CTkLabel(nav, text="Status", font=FONT_LBL,
                     text_color=C["muted"]).pack(anchor="w", padx=16)
        self._status_lbl = ctk.CTkLabel(nav, text="", font=FONT_LBL, text_color=C["ok"])
        self._status_lbl.pack(anchor="w", padx=16, pady=4)

        # pages
        self._pf = ctk.CTkFrame(self, fg_color=C["bg"], corner_radius=0)
        self._pf.pack(side="left", fill="both", expand=True)

        self._pages = {
            "analysis": AnalysisPage(self._pf, engine_ref=lambda: self._engine),
            "settings": SettingsPage(self._pf, self._areas, self._order_types,
                                     on_save=self._saved),
        }
        self._show("analysis")
        self._update_status()

    def _show(self, pid):
        for p, f in self._pages.items():
            f.pack(fill="both", expand=True) if p == pid else f.pack_forget()
        for p, b in self._nav_btns.items():
            b.configure(text_color=C["text"] if p == pid else C["muted"],
                        fg_color=C["card"]    if p == pid else "transparent")

    def _saved(self):
        self._engine = self._make_engine()
        self._pages["analysis"].refresh()
        self._update_status()

    def _update_status(self):
        snap = self._engine.snapshot(1.0)
        nb, nw = len(snap.bottlenecks), len(snap.warnings)
        if nb:
            self._status_lbl.configure(text=f"⚠ {nb} over capacity", text_color=C["critical"])
        elif nw:
            self._status_lbl.configure(text=f"△ {nw} near limit", text_color=C["warn"])
        else:
            self._status_lbl.configure(text="✓ All OK at ×1.0", text_color=C["ok"])


if __name__ == "__main__":
    app = WarehouseApp()
    app.mainloop()
