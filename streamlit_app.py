"""
Warehouse Capacity Planner — Streamlit Web App
Run:  streamlit run streamlit_app.py
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import copy, sys, os

sys.path.insert(0, os.path.dirname(__file__))

from config import (
    StorageArea, OrderType,
    StorageSplit, CustomerSplit, KittingSplit,
    ZONE_NAMES, ZONE_FLOW_ORDER,
    DEFAULT_AREAS, DEFAULT_ORDER_TYPES,
)
from engine import WarehouseEngine
import database as db

st.set_page_config(
    page_title="Warehouse Capacity Planner",
    page_icon="⬡", layout="wide",
    initial_sidebar_state="expanded",
)

ZONE_COLORS = {
    "600": "#4f6ef7", "SMART_BULK": "#7c5cfc",
    "400": "#06b6d4", "300": "#10b981",
    "200": "#f59e0b", "100": "#ef4444",
}

def status_color(pct):
    if pct >= 100: return "#ef4444"
    if pct >= 85:  return "#f59e0b"
    if pct >= 70:  return "#f97316"
    return "#22c55e"

def status_label(pct):
    if pct >= 100: return "🔴 OVER CAPACITY"
    if pct >= 85:  return "🟡 CRITICAL"
    if pct >= 70:  return "🟠 WARNING"
    return "🟢 OK"


# ── unit system ───────────────────────────────────────────────────────────────
# Internal storage is always cubic feet.
# All inputs/outputs convert to/from the selected display unit.

UNITS = {
    "cu ft":  {"label": "cu ft",  "symbol": "ft³",  "to_cuft": 1.0,          "from_cuft": 1.0},
    "cu in":  {"label": "cu in",  "symbol": "in³",  "to_cuft": 1/1728,       "from_cuft": 1728.0},
    "cu cm":  {"label": "cu cm",  "symbol": "cm³",  "to_cuft": 1/28316.8,    "from_cuft": 28316.8},
    "cu m":   {"label": "cu m",   "symbol": "m³",   "to_cuft": 35.3147,      "from_cuft": 1/35.3147},
}

def to_display(cuft_value: float) -> float:
    """Convert internal cu ft value to display unit."""
    unit = st.session_state.get("display_unit", "cu ft")
    return cuft_value * UNITS[unit]["from_cuft"]

def to_cuft(display_value: float) -> float:
    """Convert display unit value to internal cu ft."""
    unit = st.session_state.get("display_unit", "cu ft")
    return display_value * UNITS[unit]["to_cuft"]

def unit_label() -> str:
    unit = st.session_state.get("display_unit", "cu ft")
    return UNITS[unit]["symbol"]

def fmt_vol(cuft_value: float, decimals: int = 1) -> str:
    """Format a volume in the current display unit."""
    return f"{to_display(cuft_value):,.{decimals}f} {unit_label()}"


# ── Single-file CSV template ─────────────────────────────────────────────────
# One CSV with a SECTION column keeps everything together and stays
# human-readable — open in Excel, fill in, upload back.
#
# Format:
#   Rows starting with # are instructions (ignored on import)
#   section=AREAS      → storage area rows
#   section=ORDERS     → order type rows
#   All other columns  → field values (blanks are allowed for optional fields)

import io
import csv as csv_module
from datetime import datetime

# Column layout shared by both sections (unused columns are blank per row)
# Areas table — its own clean header, no blank columns
AREA_COLS = [
    "area_id", "area_name", "zone",
    "volume_cuft", "avg_box_size_cuft", "efficiency", "units_per_box",
    "is_staging", "max_concurrent_boxes",
]

# Orders table — its own clean header, no blank columns
ORDER_COLS = [
    "order_id", "order_name",
    "daily_volume", "avg_units_per_order",
    "paper_pct", "consumable_pct",
    "cust1_pct", "cust2_pct",
    "packout_pct", "kitting_pct",
]


def config_to_csv_bytes(areas, order_types) -> bytes:
    """
    Build a single CSV with two clean tables stacked vertically:
      [AREAS] header row + area rows
      blank line
      [ORDERS] header row + order rows
    Each table has only its own relevant columns — no confusing blank cells.
    """
    buf = io.StringIO()
    w = csv_module.writer(buf)

    # ── instructions ─────────────────────────────────────────────────────────
    w.writerow(["WAREHOUSE CAPACITY PLANNER - CONFIGURATION TEMPLATE"])
    w.writerow(["Lines starting with # are instructions and are ignored on upload."])
    w.writerow(["Edit the numbers below in Excel, save as CSV (not xlsx), then upload."])
    w.writerow([])
    w.writerow(["# HOW THIS FILE IS ORGANIZED"])
    w.writerow(["# Table 1 [AREAS]  = storage area settings"])
    w.writerow(["# Table 2 [ORDERS] = order type settings"])
    w.writerow(["# Do not rename area_id or order_id values - they are used as keys."])
    w.writerow(["# Do not add or remove columns. Leave a cell blank only where noted."])
    w.writerow([])
    w.writerow(["# AREAS COLUMN GUIDE"])
    w.writerow(["#   area_id               unique key - do not change"])
    w.writerow(["#   area_name             display name - safe to edit"])
    w.writerow(["#   zone                  zone code - do not change unless restructuring"])
    w.writerow(["#   volume_cuft           total cubic feet of the storage area"])
    w.writerow(["#   avg_box_size_cuft     average size of one box in cubic feet"])
    w.writerow(["#   efficiency            usable fraction 0-1, e.g. 0.75 = 75% after aisles/racking"])
    w.writerow(["#   units_per_box         average units that fit in one box in this area"])
    w.writerow(["#   is_staging            TRUE or FALSE - leave as-is unless you know what this means"])
    w.writerow(["#   max_concurrent_boxes  hard cap on boxes worked at once - leave BLANK for no cap"])
    w.writerow([])
    w.writerow(["# ORDERS COLUMN GUIDE"])
    w.writerow(["#   order_id              unique key - do not change"])
    w.writerow(["#   order_name            display name - safe to edit"])
    w.writerow(["#   daily_volume          number of orders per day"])
    w.writerow(["#   avg_units_per_order   average units per order"])
    w.writerow(["#   paper_pct + consumable_pct        must add up to 100"])
    w.writerow(["#   cust1_pct + cust2_pct             must add up to 100"])
    w.writerow(["#   packout_pct + kitting_pct         must add up to 100"])
    w.writerow([])

    # ── Table 1: AREAS ───────────────────────────────────────────────────────
    w.writerow(["[AREAS]"])
    w.writerow(AREA_COLS)
    for a in areas:
        w.writerow([
            a.id, a.name, a.zone,
            a.volume_cuft, a.avg_box_size_cuft, a.efficiency, a.units_per_box,
            "TRUE" if a.is_staging else "FALSE",
            a.max_concurrent_boxes if a.max_concurrent_boxes is not None else "",
        ])

    w.writerow([])

    # ── Table 2: ORDERS ──────────────────────────────────────────────────────
    w.writerow(["[ORDERS]"])
    w.writerow(ORDER_COLS)
    for ot in order_types:
        w.writerow([
            ot.id, ot.name,
            ot.daily_volume, ot.avg_units_per_order,
            ot.storage_split.paper_pct, ot.storage_split.consumable_pct,
            ot.customer_split.cust1_pct, ot.customer_split.cust2_pct,
            ot.kitting_split.packout_pct, ot.kitting_split.kitting_pct,
        ])

    return buf.getvalue().encode("utf-8")


def csv_bytes_to_config(file_bytes):
    """
    Parse the two-table CSV format produced by config_to_csv_bytes.
    Looks for [AREAS] and [ORDERS] section markers, reads the header
    row immediately after each, then reads rows until a blank line
    or the next section marker.
    """
    text = file_bytes.decode("utf-8-sig")  # handles Excel's BOM prefix
    raw_lines = text.splitlines()

    def find_section(marker):
        for i, line in enumerate(raw_lines):
            if line.strip().upper() == marker:
                return i
        return -1

    def read_table(start_idx):
        """Read header + data rows starting right after the [SECTION] marker."""
        if start_idx == -1:
            return []
        rows_text = []
        i = start_idx + 1
        while i < len(raw_lines):
            line = raw_lines[i]
            stripped = line.strip()
            if stripped == "" or stripped.startswith("["):
                break
            if not stripped.startswith("#"):
                rows_text.append(line)
            i += 1
        if not rows_text:
            return []
        reader = csv_module.DictReader(io.StringIO(chr(10).join(rows_text)))
        return list(reader)

    area_rows  = read_table(find_section("[AREAS]"))
    order_rows = read_table(find_section("[ORDERS]"))

    areas, order_types = [], []

    for row in area_rows:
        max_b = (row.get("max_concurrent_boxes") or "").strip()
        max_b = int(float(max_b)) if max_b else None
        areas.append(StorageArea(
            id=row["area_id"].strip(),
            name=row["area_name"].strip(),
            zone=row["zone"].strip(),
            volume_cuft=float(row["volume_cuft"]),
            avg_box_size_cuft=float(row["avg_box_size_cuft"]),
            efficiency=float(row["efficiency"]),
            units_per_box=float(row["units_per_box"]),
            is_staging=(row.get("is_staging","FALSE").strip().upper() == "TRUE"),
            max_concurrent_boxes=max_b,
        ))

    for row in order_rows:
        order_types.append(OrderType(
            id=row["order_id"].strip(),
            name=row["order_name"].strip(),
            daily_volume=int(float(row["daily_volume"])),
            avg_units_per_order=int(float(row["avg_units_per_order"])),
            storage_split=StorageSplit(
                paper_pct=float(row["paper_pct"]),
                consumable_pct=float(row["consumable_pct"])),
            customer_split=CustomerSplit(
                cust1_pct=float(row["cust1_pct"]),
                cust2_pct=float(row["cust2_pct"])),
            kitting_split=KittingSplit(
                packout_pct=float(row["packout_pct"]),
                kitting_pct=float(row["kitting_pct"])),
        ))

    if not areas:
        raise ValueError(
            "No area rows found under [AREAS]. Make sure the [AREAS] marker "
            "and its header row are intact and not modified."
        )
    if not order_types:
        raise ValueError(
            "No order rows found under [ORDERS]. Make sure the [ORDERS] marker "
            "and its header row are intact and not modified."
        )

    return areas, order_types

if "areas" not in st.session_state:
    _loaded_areas, _loaded_orders = db.load_all()
    st.session_state.areas       = [copy.deepcopy(a) for a in _loaded_areas]
    st.session_state.order_types = [copy.deepcopy(o) for o in _loaded_orders]

def get_engine():
    return WarehouseEngine(
        areas=[copy.deepcopy(a) for a in st.session_state.areas],
        order_types=[copy.deepcopy(o) for o in st.session_state.order_types],
    )

# ── sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⬡ Warehouse Planner")
    st.markdown("---")
    page = st.radio("Navigate",
        ["📦 Analysis", "🏭 Material flow", "⚙️ Settings"],
        label_visibility="collapsed")
    st.markdown("---")
    if db.is_db_configured():
        st.caption("🟢 Connected to database — changes persist")
    else:
        st.caption("⚪ No database configured — changes are session-only")
    st.markdown("---")
    st.markdown("**Quick status at x1.0**")
    _snap = get_engine().snapshot(1.0)
    nb, nw = len(_snap.bottlenecks), len(_snap.warnings)
    if nb:   st.error(f"⚠️ {nb} area(s) over capacity")
    elif nw: st.warning(f"△ {nw} area(s) near limit")
    else:    st.success("✓ All areas OK")
    st.markdown("---")
    st.caption("**Volume unit**")
    if "display_unit" not in st.session_state:
        st.session_state.display_unit = "cu ft"
    selected_unit = st.selectbox(
        "Unit", list(UNITS.keys()),
        index=list(UNITS.keys()).index(st.session_state.display_unit),
        label_visibility="collapsed", key="unit_selector")
    if selected_unit != st.session_state.display_unit:
        st.session_state.display_unit = selected_unit
        st.rerun()
    st.markdown("---")
    st.caption("**Zone legend**")
    for zone, name in [("600","Paper"),("SMART_BULK","Smart Bulk"),
                        ("400","Consumables"),("300","Cust. Spec 1"),
                        ("200","Cust. Spec 2"),("100","Final")]:
        c = ZONE_COLORS.get(zone, "#888")
        st.markdown(
            '<span style="display:inline-block;width:10px;height:10px;'
            'border-radius:2px;background:' + c + ';margin-right:6px"></span>'
            '<small>' + zone + ' – ' + name + '</small>',
            unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  MATERIAL FLOW PAGE
# ═══════════════════════════════════════════════════════════════════════════════

if page == "🏭 Material flow":
    st.title("🏭 Material flow")
    st.caption("How material moves through the warehouse — from inbound to final shipment.")

    def make_flow_diagram(engine):
        """
        Improved flow diagram using scatter traces for proper connected arrows.
        Layout (x, y in data coords 0-100):
          Row 1 (y=90): Inbound Orders (centre)
          Row 2 (y=70): 600-Paper (left), 400-Consumables (right)
          Row 3 (y=50): SmartBulk (left), 300-CS1 (centre-left), 200-CS2 (right)
          Row 4 (y=30): Kitting-300 (centre-left), Kitting-200 (right)
          Row 5 (y=10): 100-Final (centre)
        """
        snap = engine.snapshot(1.0)
        fl   = snap.flow
        fig  = go.Figure()

        # ── coordinate system: 0-100 x, 0-100 y ─────────────────────────
        # node centres
        N = {
            "orders":  (50, 90),
            "z600":    (22, 70),
            "z400":    (78, 70),
            "smb":     (22, 50),
            "z300":    (48, 50),
            "z200":    (78, 50),
            "kit300":  (48, 30),
            "kit200":  (78, 30),
            "final":   (50, 10),
        }

        def add_arrow(x0, y0, x1, y1, color, dash="solid", width=2):
            """Draw a line + arrowhead using scatter + annotation."""
            fig.add_trace(go.Scatter(
                x=[x0, x1], y=[y0, y1], mode="lines",
                line=dict(color=color, width=width, dash=dash),
                hoverinfo="skip", showlegend=False))
            # arrowhead: small marker at end styled as triangle
            import math
            dx, dy = x1-x0, y1-y0
            length = math.sqrt(dx*dx + dy*dy) or 1
            # place marker slightly back from tip so it looks centred
            mx = x1 - dx/length*1.5
            my = y1 - dy/length*1.5
            angle = math.degrees(math.atan2(dy, dx))
            fig.add_trace(go.Scatter(
                x=[x1], y=[y1], mode="markers",
                marker=dict(
                    symbol="arrow", size=14, color=color,
                    angle=angle - 90,
                    line=dict(width=0)),
                hoverinfo="skip", showlegend=False))

        def add_node(x, y, title, subtitle, tc, bg, bc, w=18, h=7):
            """Draw a rounded rectangle node."""
            # shadow/glow
            fig.add_shape(type="rect",
                x0=x-w/2+0.4, y0=y-h/2-0.4,
                x1=x+w/2+0.4, y1=y+h/2-0.4,
                xref="x", yref="y",
                fillcolor="rgba(0,0,0,0.4)", line=dict(width=0))
            # main box
            fig.add_shape(type="rect",
                x0=x-w/2, y0=y-h/2,
                x1=x+w/2, y1=y+h/2,
                xref="x", yref="y",
                fillcolor=bg,
                line=dict(color=bc, width=2))
            # title
            fig.add_annotation(
                x=x, y=y + (1.2 if subtitle else 0),
                text="<b>" + title + "</b>",
                showarrow=False, font=dict(size=13, color=tc),
                xref="x", yref="y", align="center")
            if subtitle:
                fig.add_annotation(
                    x=x, y=y - 1.8,
                    text="<span style='font-size:10px;color:#9ca3af'>" + subtitle + "</span>",
                    showarrow=False, font=dict(size=10, color="#9ca3af"),
                    xref="x", yref="y", align="center")

        def edge_label(x, y, text, color, size=10):
            fig.add_annotation(
                x=x, y=y, text="<i>" + text + "</i>",
                showarrow=False, font=dict(size=size, color=color),
                xref="x", yref="y", align="center",
                bgcolor="rgba(15,17,23,0.75)", borderpad=2)

        def rule_badge(x, y, badge, detail, color):
            fig.add_annotation(
                x=x, y=y,
                text="<b>" + badge + "</b>  <span style='color:#6b7280;font-size:10px'>" + detail + "</span>",
                showarrow=False, font=dict(size=11, color=color),
                xref="x", yref="y", align="left",
                bgcolor="rgba(15,17,23,0.8)",
                bordercolor=color, borderwidth=1, borderpad=4)

        # ── nodes ────────────────────────────────────────────────────────
        add_node(*N["orders"], "Inbound Orders",     "",                    "#c7d2fe", "#1e2235", "#4f6ef7", w=20, h=6)
        add_node(*N["z600"],   "600 – Paper",        "Raw paper storage",   "#a0a8f0", "#1a2040", "#4f6ef7")
        add_node(*N["z400"],   "400 – Consumables",  "Raw consumables",     "#67d8f0", "#0a2025", "#06b6d4")
        add_node(*N["smb"],    "Smart Bulk",         "Paper staging",       "#c4b5fd", "#1e1540", "#7c5cfc")
        add_node(*N["z300"],   "300 – Cust. Spec 1", "Customer area 1",     "#6ee7b7", "#0a2018", "#10b981")
        add_node(*N["z200"],   "200 – Cust. Spec 2", "Customer area 2",     "#fcd34d", "#2a1800", "#f59e0b")
        add_node(*N["kit300"], "Kitting",            "Custom kit assembly", "#d1d5db", "#111827", "#6b7280", w=16, h=6)
        add_node(*N["kit200"], "Kitting",            "Custom kit assembly", "#d1d5db", "#111827", "#6b7280", w=16, h=6)
        add_node(*N["final"],  "100 – Final/Packout","Ready to ship",       "#fca5a5", "#2a0a0a", "#ef4444", w=24, h=6)

        # ── arrows ───────────────────────────────────────────────────────
        # Inbound → 600
        add_arrow(50, 87, 22, 73.5, "#4f6ef7")
        # Inbound → 400
        add_arrow(50, 87, 78, 73.5, "#06b6d4")
        # 600 → Smart Bulk
        add_arrow(22, 66.5, 22, 53.5, "#7c5cfc")
        # Smart Bulk → Final (direct bypass, dashed)
        add_arrow(11, 50, 11, 10, "#7c5cfc", dash="dot", width=1.5)
        add_arrow(11, 10, 38, 10, "#7c5cfc", dash="dot", width=1.5)
        # 400 → 300
        add_arrow(65, 70, 56, 53.5, "#10b981")
        # 400 → 200
        add_arrow(78, 66.5, 78, 53.5, "#f59e0b")
        # 300 → Kitting
        add_arrow(48, 46.5, 48, 33.5, "#10b981")
        # 200 → Kitting
        add_arrow(78, 46.5, 78, 33.5, "#f59e0b")
        # Kitting 300 loop back to 300 (via left side)
        add_arrow(40, 30, 40, 50, "#6b7280", dash="dash", width=1.5)
        # Kitting 200 loop back to 200 (via right side)
        add_arrow(86, 30, 86, 50, "#6b7280", dash="dash", width=1.5)
        # 300 → Final
        add_arrow(44, 46.5, 44, 13.5, "#10b981")
        # 200 → Final
        add_arrow(78, 46.5, 56, 13.5, "#f59e0b")

        # ── edge labels ──────────────────────────────────────────────────
        edge_label(34, 82,   "Paper % (Split 1)",        "#4f6ef7")
        edge_label(66, 82,   "Consumable % (Split 1)",   "#06b6d4")
        edge_label(22, 60,   "staged",                   "#7c5cfc")
        edge_label(8,  30,   "direct to 100",           "#7c5cfc")
        edge_label(59, 63,   "Cust 1 % (Split 2)",      "#10b981")
        edge_label(83, 60,   "Cust 2 % (Split 2)",       "#f59e0b")
        edge_label(52, 40,   "Kitting % (Split 3)",     "#10b981")
        edge_label(82, 40,   "Kitting % (Split 3)",     "#f59e0b")
        edge_label(37, 40,   "back to 300",              "#6b7280")
        edge_label(90, 40,   "back to 200",              "#6b7280")

        # ── rule badges ──────────────────────────────────────────────────
        rule_badge(2, 82, "SPLIT 1", "600 vs 400",           "#818cf8")
        rule_badge(2, 60, "RULE 2",  "Paper→SmartBulk→100",  "#a78bfa")
        rule_badge(2, 52, "SPLIT 2", "300 vs 200",           "#34d399")
        rule_badge(2, 32, "SPLIT 3", "Kitting loop",         "#9ca3af")
        rule_badge(2, 10, "RULE 5",  "All paths→100",        "#f87171")

        # ── live volume callouts ─────────────────────────────────────────
        fig.add_annotation(
            x=50, y=96,
            text="<b>Today at ×1.0:</b>  "
                 + "Smart Bulk " + str(int(fl.paper_to_smart_bulk)) + " boxes/day  |  "
                 + "Zn300 " + str(int(fl.consumables_to_300)) + "  |  "
                 + "Zn200 " + str(int(fl.consumables_to_200)) + " boxes/day",
            showarrow=False, font=dict(size=11, color="#94a3b8"),
            xref="x", yref="y", align="center",
            bgcolor="rgba(30,34,53,0.9)", borderpad=6,
            bordercolor="#2e3250", borderwidth=1)

        fig.update_layout(
            height=750,
            margin=dict(l=10, r=10, t=20, b=10),
            paper_bgcolor="#0f1117",
            plot_bgcolor="#0f1117",
            xaxis=dict(visible=False, range=[0, 100], fixedrange=True),
            yaxis=dict(visible=False, range=[0, 100], fixedrange=True),
            showlegend=False,
        )
        return fig

    st.plotly_chart(make_flow_diagram(get_engine()), use_container_width=True)
    st.markdown("---")

    engine = get_engine()
    snap   = engine.snapshot(1.0)
    fl     = snap.flow

    st.subheader("Live flow volumes at x1.0")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Paper to Smart Bulk",    str(int(fl.paper_to_smart_bulk))  + " boxes/day")
    c2.metric("Consumables total",      str(int(fl.consumables_total))    + " boxes/day")
    c3.metric("Consumables to Zn 300",  str(int(fl.consumables_to_300))   + " boxes/day")
    c4.metric("Consumables to Zn 200",  str(int(fl.consumables_to_200))   + " boxes/day")

    st.markdown("#### Per order type")
    for ot in engine.order_types:
        with st.expander("**" + ot.name + "**", expanded=True):
            r1, r2, r3 = st.columns(3)
            r1.metric("Daily orders",    ot.daily_volume)
            r2.metric("Avg units/order", ot.avg_units_per_order)
            r3.metric("Total units/day", str(int(ot.total_units())))

            st.caption("Split 1 - Storage")
            s1a, s1b = st.columns(2)
            s1a.metric("600 Paper %",        str(int(ot.storage_split.paper_pct))      + "%")
            s1b.metric("400 Consumables %",  str(int(ot.storage_split.consumable_pct)) + "%")

            st.caption("Split 2 - Customer (of the Consumables portion)")
            s2a, s2b = st.columns(2)
            s2a.metric("to Zone 300 %", str(int(ot.customer_split.cust1_pct)) + "%")
            s2b.metric("to Zone 200 %", str(int(ot.customer_split.cust2_pct)) + "%")

            st.caption("Split 3 - Kitting (of 300/200 material)")
            s3a, s3b, s3c = st.columns(3)
            s3a.metric("Direct to packout %", str(int(ot.kitting_split.packout_pct)) + "%")
            s3b.metric("to Kitting %",        str(int(ot.kitting_split.kitting_pct)) + "%")
            kit_boxes = sum(
                ot.boxes_in_area(a, 1.0) * (ot.kitting_split.kitting_pct / 100)
                for a in engine.areas if a.zone in ("300", "200")
            )
            s3c.metric("Kitting boxes/day", str(round(kit_boxes, 1)))

    st.markdown("---")
    st.markdown("#### Flow rules reference")
    st.markdown("""
| Rule | Logic |
|---|---|
| **Split 1 - Storage** | 600% + 400% = 100% — where the order draws its units from |
| **Rule 2 - Paper path** | 600 to Smart Bulk (same volume staged) then direct to 100 Final — bypasses 300/200 |
| **Split 2 - Customer** | 300% + 200% = 100% — how the 400 consumable portion divides between customers |
| **Split 3 - Kitting** | Packout% + Kitting% = 100% — how 300/200 material routes onward |
| **Rule 4 - Kitting loop** | Kitting returns to same zone (300 or 200) then flows to 100 normally |
| **Rule 5 - Final** | All paths converge at 100 Final/Packout before shipping |
    """)


# ═══════════════════════════════════════════════════════════════════════════════
#  SETTINGS PAGE
# ═══════════════════════════════════════════════════════════════════════════════

if page == "⚙️ Settings":
    st.title("⚙️ Settings")
    st.caption("Update area dimensions and order type splits, then click Save.")

    # ── Unit converter ───────────────────────────────────────────────────────
    with st.expander("🔁 Volume unit converter", expanded=False):
        st.caption("Enter a value in any unit to see all equivalents instantly.")
        cc1, cc2 = st.columns(2)
        conv_val = cc1.number_input("Value", value=1.0, step=0.1, key="conv_val", min_value=0.0)
        conv_from = cc2.selectbox("From unit", list(UNITS.keys()), key="conv_from")
        val_in_cuft = conv_val * UNITS[conv_from]["to_cuft"]
        st.markdown("**Equivalents:**")
        rc = st.columns(4)
        for i, (ukey, udata) in enumerate(UNITS.items()):
            converted = val_in_cuft * udata["from_cuft"]
            if ukey == conv_from:
                rc[i].metric(udata["label"], f"{converted:,.4f}", delta="← input", delta_color="off")
            else:
                rc[i].metric(udata["label"], f"{converted:,.4f}")

    st.markdown("---")
    st.subheader("Storage areas")
    area_updates = {}
    cols = st.columns(2)
    for i, area in enumerate(st.session_state.areas):
        with cols[i % 2]:
            zone_str = "Staging" if area.is_staging else "Zone " + area.zone
            with st.expander("**" + area.name + "** — " + zone_str, expanded=True):
                ul = unit_label()
                disp_vol = to_display(area.volume_cuft)
                disp_box = to_display(area.avg_box_size_cuft)
                v_vol_d = st.number_input("Volume (" + ul + ")",       value=float(disp_vol),           step=float(max(0.1, round(to_display(100),1))), key="vol_" + area.id)
                v_box_d = st.number_input("Avg box size (" + ul + ")", value=float(disp_box),           step=float(max(0.001, round(to_display(0.1),3))), key="box_" + area.id)
                v_eff   = st.number_input("Efficiency (0-1)",           value=float(area.efficiency),   step=0.01, min_value=0.1, max_value=1.0, key="eff_" + area.id)
                v_upb   = st.number_input("Units per box",              value=float(area.units_per_box),step=1.0,  min_value=1.0, key="upb_" + area.id)
                v_vol = to_cuft(v_vol_d)
                v_box = to_cuft(v_box_d)
                # Max concurrent boxes — only shown for Smart Bulk and Kitting
                v_max_boxes = None
                if area.has_box_cap or area.id in ("smart_bulk", "kitting"):
                    st.markdown("---")
                    st.markdown("**🔢 Max concurrent boxes** — hard cap for this work station")
                    st.caption("Utilization is measured against this limit, not area volume. Set to 0 to use volume-based capacity only.")
                    cur_max = area.max_concurrent_boxes if area.max_concurrent_boxes is not None else 0
                    raw_max = st.number_input("Max boxes in station at one time",
                        value=int(cur_max), step=10, min_value=0, key="maxb_" + area.id)
                    v_max_boxes = int(raw_max) if raw_max > 0 else None
                    if v_max_boxes:
                        st.info("Hard cap active: capacity = **" + str(v_max_boxes) + " boxes**")
                    else:
                        st.info("No box cap — capacity determined by volume only")
                vol_cap = int((v_vol * v_eff) / v_box) if v_box > 0 else 0
                eff_cap = min(v_max_boxes, vol_cap) if v_max_boxes else vol_cap
                cap_units = int(eff_cap * v_upb)
                st.info("Effective capacity: **" + str(eff_cap) + " boxes**  |  **" + str(cap_units) + " units**")
                area_updates[area.id] = dict(
                    volume_cuft=v_vol, avg_box_size_cuft=v_box,
                    efficiency=v_eff,  units_per_box=v_upb,
                    max_concurrent_boxes=v_max_boxes)

    st.markdown("---")
    st.subheader("Order types")
    st.caption("Configure volume and three independent splits for each order type.")

    order_updates = {}
    for ot in st.session_state.order_types:
        with st.expander("**" + ot.name + "**", expanded=True):
            c1, c2 = st.columns(2)
            v_vol = c1.number_input("Daily volume (orders)", value=int(ot.daily_volume),        step=1, min_value=1, key="ovol_" + ot.id)
            v_qty = c2.number_input("Avg units / order",     value=int(ot.avg_units_per_order), step=1, min_value=1, key="oqty_" + ot.id)

            st.markdown("---")
            st.markdown("**Split 1 - Storage** | 600% + 400% must total 100%")
            s1c1, s1c2 = st.columns(2)
            v_paper = s1c1.number_input("600 Paper %",       value=float(ot.storage_split.paper_pct),      step=1.0, min_value=0.0, max_value=100.0, key="paper_" + ot.id)
            v_cons  = s1c2.number_input("400 Consumables %", value=float(ot.storage_split.consumable_pct), step=1.0, min_value=0.0, max_value=100.0, key="cons_"  + ot.id)
            s1t = v_paper + v_cons
            if abs(s1t - 100) > 0.5:
                st.warning("Storage split totals " + str(int(s1t)) + "% — must be 100%")
            else:
                st.success("Storage split OK: " + str(int(v_paper)) + "% Paper + " + str(int(v_cons)) + "% Consumables = 100%")

            st.markdown("---")
            st.markdown("**Split 2 - Customer** | 300% + 200% must total 100%  *(applied to Consumables portion only)*")
            s2c1, s2c2 = st.columns(2)
            v_c1 = s2c1.number_input("to Zone 300 %", value=float(ot.customer_split.cust1_pct), step=1.0, min_value=0.0, max_value=100.0, key="c1_" + ot.id)
            v_c2 = s2c2.number_input("to Zone 200 %", value=float(ot.customer_split.cust2_pct), step=1.0, min_value=0.0, max_value=100.0, key="c2_" + ot.id)
            s2t = v_c1 + v_c2
            if abs(s2t - 100) > 0.5:
                st.warning("Customer split totals " + str(int(s2t)) + "% — must be 100%")
            else:
                st.success("Customer split OK: " + str(int(v_c1)) + "% Zone 300 + " + str(int(v_c2)) + "% Zone 200 = 100%")

            st.markdown("---")
            st.markdown("**Split 3 - Kitting** | Packout% + Kitting% must total 100%  *(of 300/200 material)*")
            s3c1, s3c2 = st.columns(2)
            v_pack = s3c1.number_input("Direct to packout %", value=float(ot.kitting_split.packout_pct), step=1.0, min_value=0.0, max_value=100.0, key="pack_" + ot.id)
            v_kit  = s3c2.number_input("to Kitting %",        value=float(ot.kitting_split.kitting_pct), step=1.0, min_value=0.0, max_value=100.0, key="kit_"  + ot.id)
            s3t = v_pack + v_kit
            if abs(s3t - 100) > 0.5:
                st.warning("Kitting split totals " + str(int(s3t)) + "% — must be 100%")
            else:
                st.success("Kitting split OK: " + str(int(v_pack)) + "% direct + " + str(int(v_kit)) + "% kitting = 100%")

            order_updates[ot.id] = dict(
                daily_volume=v_vol, avg_units_per_order=v_qty,
                paper_pct=v_paper, consumable_pct=v_cons,
                cust1_pct=v_c1,    cust2_pct=v_c2,
                packout_pct=v_pack, kitting_pct=v_kit)

    st.markdown("---")
    if st.button("Save & recalculate", type="primary", use_container_width=True):
        area_map = {a.id: a for a in st.session_state.areas}
        for aid, u in area_updates.items():
            a = area_map[aid]
            a.volume_cuft           = u["volume_cuft"]
            a.avg_box_size_cuft     = u["avg_box_size_cuft"]
            a.efficiency            = u["efficiency"]
            a.units_per_box         = u["units_per_box"]
            if "max_concurrent_boxes" in u:
                a.max_concurrent_boxes = u["max_concurrent_boxes"]

        ot_map = {o.id: o for o in st.session_state.order_types}
        for oid, u in order_updates.items():
            ot = ot_map[oid]
            ot.daily_volume        = u["daily_volume"]
            ot.avg_units_per_order = u["avg_units_per_order"]
            ot.storage_split  = StorageSplit(paper_pct=u["paper_pct"],      consumable_pct=u["consumable_pct"])
            ot.customer_split = CustomerSplit(cust1_pct=u["cust1_pct"],     cust2_pct=u["cust2_pct"])
            ot.kitting_split  = KittingSplit(packout_pct=u["packout_pct"],  kitting_pct=u["kitting_pct"])

        st.success("Settings saved.")
        if db.is_db_configured():
            saved_ok = db.save_all(st.session_state.areas, st.session_state.order_types)
            if saved_ok:
                st.success("✅ Saved to database — values will persist across sessions.")
            else:
                st.warning("⚠️ Saved locally but database write failed — check the error above.")
        else:
            st.info("ℹ️ No database configured — values are session-only and will reset on refresh. See README to set up Supabase.")
        st.rerun()

    st.markdown("---")
    st.subheader("💾 Save / Load configuration (CSV)")
    st.caption(
        "Download the template, fill it in Excel or any spreadsheet app, "
        "then upload it here to apply your values."
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    csv_data = config_to_csv_bytes(st.session_state.areas, st.session_state.order_types)

    col_dl, col_ul = st.columns(2)

    with col_dl:
        st.markdown("**Step 1 — Download template**")
        st.caption(
            "Contains your current values pre-filled. Edit numbers in Excel, "
            "save as CSV, and upload. Comment rows (starting with #) explain each field."
        )
        st.download_button(
            "⬇️ Download configuration template (CSV)",
            data=csv_data,
            file_name="warehouse_config_" + timestamp + ".csv",
            mime="text/csv",
            use_container_width=True,
        )

    with col_ul:
        st.markdown("**Step 2 — Upload filled template**")
        st.caption("Upload the same CSV after editing. Both areas and order types load at once.")
        uploaded = st.file_uploader(
            "Choose CSV file", type=["csv"], key="upload_config",
            label_visibility="collapsed")

        if uploaded is not None:
            try:
                preview_areas, preview_orders = csv_bytes_to_config(uploaded.getvalue())
                st.success(
                    "Parsed OK — " + str(len(preview_areas)) + " areas  ·  "
                    + str(len(preview_orders)) + " order types. Click Apply to load.")

                # show a quick preview so user can verify before applying
                with st.expander("Preview imported values", expanded=False):
                    st.markdown("**Areas**")
                    st.dataframe(
                        pd.DataFrame([{
                            "Area": a.name, "Zone": a.zone,
                            "Volume (cu ft)": a.volume_cuft,
                            "Box size (cu ft)": a.avg_box_size_cuft,
                            "Efficiency": a.efficiency,
                            "Units/box": a.units_per_box,
                            "Max boxes": a.max_concurrent_boxes or "—",
                        } for a in preview_areas]),
                        use_container_width=True, hide_index=True)
                    st.markdown("**Order types**")
                    st.dataframe(
                        pd.DataFrame([{
                            "Order": ot.name,
                            "Daily vol": ot.daily_volume,
                            "Avg units": ot.avg_units_per_order,
                            "Paper %": ot.storage_split.paper_pct,
                            "Consumable %": ot.storage_split.consumable_pct,
                            "Cust1 %": ot.customer_split.cust1_pct,
                            "Cust2 %": ot.customer_split.cust2_pct,
                            "Packout %": ot.kitting_split.packout_pct,
                            "Kitting %": ot.kitting_split.kitting_pct,
                        } for ot in preview_orders]),
                        use_container_width=True, hide_index=True)

                if st.button("✅ Apply imported configuration", type="primary", use_container_width=True):
                    st.session_state.areas = preview_areas
                    st.session_state.order_types = preview_orders
                    if db.is_db_configured():
                        db.save_all(preview_areas, preview_orders)
                        st.success("Configuration loaded and saved to database.")
                    else:
                        st.success("Configuration loaded (session only — no database configured).")
                    st.rerun()

            except Exception as e:
                st.error("Could not parse CSV: " + str(e))
                st.caption("Make sure you uploaded the correct template file without renaming columns.")

    st.markdown("---")
    st.subheader("🗄️ Database controls")
    if db.is_db_configured():
        st.success("Database connected — Save & recalculate above also writes here automatically.")
        dbc1, dbc2 = st.columns(2)
        with dbc1:
            if st.button("🔄 Reload from database", use_container_width=True):
                _areas, _orders = db.load_all()
                st.session_state.areas = _areas
                st.session_state.order_types = _orders
                st.success("Reloaded from database.")
                st.rerun()
        with dbc2:
            if st.button("↩️ Reset to factory defaults", use_container_width=True):
                st.session_state.areas = [copy.deepcopy(a) for a in DEFAULT_AREAS]
                st.session_state.order_types = [copy.deepcopy(o) for o in DEFAULT_ORDER_TYPES]
                db.save_all(st.session_state.areas, st.session_state.order_types)
                st.success("Reset to defaults and saved to database.")
                st.rerun()
    else:
        st.warning(
            "No database connected. Values only persist for this browser session. "
            "See README.md for the one-time Supabase setup (free, ~5 minutes)."
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  ANALYSIS PAGE
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "📦 Analysis":
    st.title("📦 Analysis")

    multiplier = st.slider(
        "Volume multiplier", min_value=1.0, max_value=10.0,
        value=1.0, step=0.1, format="x%.1f")

    engine = get_engine()
    snap   = engine.snapshot(multiplier=multiplier)

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Capacity (boxes)", str(snap.total_capacity_boxes))
    k2.metric("Capacity (units)", str(snap.total_capacity_units))
    k3.metric("Load (boxes)",     str(int(snap.total_load_boxes)))
    k4.metric("Overall util.",    str(round(snap.overall_utilization, 1)) + "%")
    k5.metric("Over capacity",    len(snap.bottlenecks))
    k6.metric("Near limit",       len(snap.warnings))

    for a in snap.bottlenecks:
        st.error("BOTTLENECK — " + a.area.name + " at " + str(round(a.utilization_pct, 1)) + "%")
    for a in snap.warnings:
        st.warning("WARNING — " + a.area.name + " at " + str(round(a.utilization_pct, 1)) + "%")
    if not snap.bottlenecks and not snap.warnings:
        st.success("All areas within capacity at x" + str(multiplier))

    st.markdown("---")
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Overview", "Area detail", "Growth table", "Bottlenecks", "BOM breakdown"])

    with tab1:
        st.subheader("Area utilization")
        area_names = [a.area.name for a in snap.areas]
        utils      = [round(a.utilization_pct, 1) for a in snap.areas]
        colors     = [status_color(p) for p in utils]

        fig = go.Figure(go.Bar(
            y=area_names, x=utils, orientation="h",
            marker_color=colors,
            text=[str(p) + "%" for p in utils], textposition="outside",
            customdata=list(zip(
                [round(a.load_boxes, 0) for a in snap.areas],
                [a.capacity_boxes       for a in snap.areas],
                [round(a.load_units, 0) for a in snap.areas],
                [a.capacity_units       for a in snap.areas],
                [a.area.units_per_box   for a in snap.areas],
            )),
            hovertemplate=(
                "<b>%{y}</b><br>Util: %{x:.1f}%<br>"
                "Boxes: %{customdata[0]:,.0f} / %{customdata[1]:,}<br>"
                "Units: %{customdata[2]:,.0f} / %{customdata[3]:,}<br>"
                "Units/box: %{customdata[4]:.0f}<extra></extra>"
            ),
        ))
        fig.add_vline(x=85,  line_dash="dot", line_color="#f59e0b", annotation_text="85%")
        fig.add_vline(x=100, line_dash="dot", line_color="#ef4444", annotation_text="100%")
        fig.update_layout(
            xaxis=dict(title="Utilization %", range=[0, 115]),
            yaxis=dict(autorange="reversed"),
            height=320, margin=dict(l=10, r=60, t=20, b=40),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)

        rows = []
        for a in snap.areas:
            cap_src = "box cap" if a.area.has_box_cap else "volume"
            rows.append({
                "Area":         a.area.name,
                "Zone":         a.area.zone,
                "Units/box":    int(a.area.units_per_box),
                "Load (boxes)": str(int(a.load_boxes)),
                "Cap (boxes)":  str(a.capacity_boxes) + (" 🔢" if a.area.has_box_cap else ""),
                "Load (units)": str(int(a.load_units)),
                "Cap (units)":  str(a.capacity_units),
                "Cap source":   cap_src,
                "Util %":       str(round(a.utilization_pct, 1)) + "%",
                "Status":       status_label(a.utilization_pct),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption("🔢 = hard box cap active — utilization measured against max concurrent boxes, not volume")

        st.subheader("Zone summary")
        zone_df = engine.zone_summary(multiplier)
        zcols = st.columns(len(zone_df))
        for i, (_, row) in enumerate(zone_df.iterrows()):
            pct = row["utilization_pct"]
            zc  = ZONE_COLORS.get(row["zone_code"], "#888")
            with zcols[i]:
                st.markdown(
                    "<div style='text-align:center;padding:10px;border:1px solid #2e3250;"
                    "border-radius:10px;border-left:4px solid " + zc + "'>"
                    "<div style='font-size:11px;color:#6b7280'>Zone " + str(row["zone_code"]) + "</div>"
                    "<div style='font-weight:600;font-size:12px'>" + str(row["zone_name"]) + "</div>"
                    "<div style='font-size:22px;font-weight:700;color:" + status_color(pct) + "'>" + str(pct) + "%</div>"
                    "<div style='font-size:11px;color:#6b7280'>" + str(row["capacity_boxes"]) + " box cap</div>"
                    "</div>", unsafe_allow_html=True)

    with tab2:
        st.subheader("Area detail — order contributions")
        for a in snap.areas:
            cap_tag = " 🔢 box cap" if a.area.has_box_cap else ""
            with st.expander(
                a.area.name + " — " + str(round(a.utilization_pct, 1)) + "%  |  "
                + str(int(a.load_boxes)) + "/" + str(a.capacity_boxes) + " boxes" + cap_tag + "  |  "
                + str(int(a.load_units)) + "/" + str(a.capacity_units) + " units  |  "
                + str(int(a.area.units_per_box)) + " u/box  " + status_label(a.utilization_pct),
                expanded=True):
                st.progress(min(a.utilization_pct / 100, 1.0))
                if a.contributing_orders:
                    rows = []
                    for oid, boxes in sorted(
                        a.contributing_orders.items(), key=lambda x: x[1], reverse=True):
                        rows.append({
                            "Order":     oid,
                            "Boxes":     str(round(boxes, 1)),
                            "Units":     str(int(boxes * a.area.units_per_box)),
                            "% of area": str(round(boxes / a.load_boxes * 100, 1)) + "%" if a.load_boxes else "—",
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                else:
                    st.caption("No orders currently load this area.")

    with tab3:
        st.subheader("Growth table")
        df = engine.growth_table(max_multiplier=10.0, steps=18)
        pivot = df.pivot_table(
            index="multiplier", columns="area",
            values="utilization_pct", aggfunc="first").reset_index()
        area_names_list = [a.name for a in engine.areas]

        fig3 = go.Figure(data=go.Heatmap(
            z=[[row.get(n, 0) for n in area_names_list] for _, row in pivot.iterrows()],
            x=area_names_list,
            y=["x" + str(round(row["multiplier"], 1)) for _, row in pivot.iterrows()],
            colorscale=[[0,"#1a3a2a"],[0.70,"#22c55e"],[0.85,"#f59e0b"],[1,"#ef4444"]],
            zmin=0, zmax=110,
            text=[[str(int(row.get(n, 0))) + "%" for n in area_names_list] for _, row in pivot.iterrows()],
            texttemplate="%{text}",
            hovertemplate="Area: %{x}<br>Mult: %{y}<br>Util: %{z:.1f}%<extra></extra>",
        ))
        fig3.update_layout(height=500, margin=dict(l=10,r=10,t=20,b=10),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(tickangle=-30))
        st.plotly_chart(fig3, use_container_width=True)

        def color_cell(val):
            try:
                v = float(str(val).replace("%", ""))
                if v >= 100: return "background-color:#7f1d1d;color:#fca5a5"
                if v >= 85:  return "background-color:#78350f;color:#fde68a"
                if v >= 70:  return "background-color:#431407;color:#fed7aa"
                return "background-color:#052e16;color:#86efac"
            except:
                return ""

        dp = pivot.copy()
        dp["multiplier"] = dp["multiplier"].apply(lambda x: "x" + str(round(x, 1)))
        dp = dp.rename(columns={"multiplier": "Mult."})
        for col in area_names_list:
            if col in dp.columns:
                dp[col] = dp[col].apply(lambda x: str(int(x)) + "%")
        st.dataframe(dp.style.map(color_cell, subset=area_names_list),
                     use_container_width=True, hide_index=True)

    with tab4:
        st.subheader("Bottleneck sequence")
        seq_100 = engine.bottleneck_sequence(threshold_pct=100.0, max_mult=20.0)
        seq_85  = engine.bottleneck_sequence(threshold_pct=85.0,  max_mult=20.0)

        if not seq_100:
            st.success("No areas hit 100% within x20 volume.")
        else:
            for rank, (mult, area_name, _) in enumerate(seq_100, 1):
                icon = "🔴" if rank == 1 else "🟡" if rank == 2 else "🟠"
                note = " — Address first" if rank == 1 else ""
                st.markdown(
                    "#" + str(rank) + " " + icon + " **" + area_name
                    + "** hits capacity at **x" + str(mult) + "**" + note)

        st.markdown("---")
        st.subheader("85% warning threshold")
        if seq_85:
            st.dataframe(
                pd.DataFrame([{"Area": n, "Reaches 85% at": "x" + str(m)} for m, n, _ in seq_85]),
                use_container_width=True, hide_index=True)
            fig4 = go.Figure()
            for mult, name, _ in reversed(seq_85):
                fig4.add_trace(go.Bar(
                    y=[name], x=[mult], orientation="h",
                    marker_color=status_color(85),
                    text=["x" + str(mult)], textposition="outside",
                    showlegend=False))
            fig4.add_vline(x=multiplier, line_dash="dash", line_color="#4f6ef7",
                           annotation_text="Current x" + str(multiplier))
            fig4.update_layout(
                xaxis=dict(title="Multiplier", range=[0, 22]),
                yaxis=dict(autorange="reversed"),
                height=280, margin=dict(l=10,r=60,t=40,b=40),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                barmode="overlay")
            st.plotly_chart(fig4, use_container_width=True)
        else:
            st.success("No areas reach 85% within x20 volume.")

    with tab5:
        st.subheader("BOM breakdown")
        bom_df = engine.order_bom_summary(multiplier)
        if not bom_df.empty:
            fig5 = px.bar(
                bom_df, x="order", y="boxes", color="zone_name", barmode="group",
                labels={"boxes":"Boxes/day","order":"Order type","zone_name":"Zone"},
                title="Daily boxes by zone and order type",
                color_discrete_map={
                    ZONE_NAMES["600"]: ZONE_COLORS["600"],
                    ZONE_NAMES["400"]: ZONE_COLORS["400"],
                    ZONE_NAMES["300"]: ZONE_COLORS["300"],
                    ZONE_NAMES["200"]: ZONE_COLORS["200"],
                })
            fig5.update_layout(height=320, margin=dict(l=10,r=10,t=40,b=40),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig5, use_container_width=True)

            disp = bom_df[["order_name","zone_name","pct_of_total","units","units_per_box","boxes"]].copy()
            disp.columns = ["Order","Zone","% of total","Units/day","Units per box","Boxes/day"]
            st.dataframe(disp, use_container_width=True, hide_index=True)
