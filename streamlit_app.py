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

# ── Global "compact mode" — shrinks padding, gaps, headers, tables, metrics ──
# Injected once; applies to every page. Pure CSS, so unmatched rules are no-ops.
st.markdown("""
<style>
/* reclaim the big empty margin at the top + sides of the page */
.block-container, [data-testid="stMainBlockContainer"] {
    padding-top: 1.1rem !important;
    padding-bottom: 1rem !important;
    padding-left: 1.6rem !important;
    padding-right: 1.6rem !important;
    max-width: 100% !important;
}
/* tighten the vertical gap between stacked elements */
[data-testid="stVerticalBlock"] { gap: 0.45rem !important; }
[data-testid="stHorizontalBlock"] { gap: 0.5rem !important; }
/* smaller, tighter headings */
h1, [data-testid="stHeading"] h1 { font-size: 1.5rem  !important; margin: .1rem 0 .3rem 0 !important; }
h2 { font-size: 1.2rem  !important; margin: .2rem 0 !important; padding: 0 !important; }
h3 { font-size: 1.02rem !important; margin: .15rem 0 !important; padding: 0 !important; }
/* captions + small text */
[data-testid="stCaptionContainer"], .stCaption { font-size: .74rem !important; margin: 0 !important; }
/* markdown paragraph spacing */
[data-testid="stMarkdownContainer"] p { margin-bottom: .25rem !important; line-height: 1.3 !important; }
/* thin dividers */
hr { margin: .35rem 0 !important; }
/* compact metrics */
[data-testid="stMetric"] { padding: .2rem .4rem !important; }
[data-testid="stMetricValue"] { font-size: 1.05rem !important; }
[data-testid="stMetricLabel"] { font-size: .68rem !important; }
[data-testid="stMetricLabel"] p { font-size: .68rem !important; }
/* dataframes / data_editor: smaller font + rows */
[data-testid="stDataFrame"], [data-testid="stDataEditor"] { font-size: .78rem !important; }
[data-testid="stDataFrame"] div, [data-testid="stDataEditor"] div { line-height: 1.15 !important; }
/* expanders: tight header + body */
[data-testid="stExpander"] summary { padding: .3rem .55rem !important; font-size: .85rem !important; }
[data-testid="stExpander"] [data-testid="stExpanderDetails"] { padding: .3rem .6rem !important; }
/* buttons: less padding */
.stButton button, [data-testid="stDownloadButton"] button, [data-testid="baseButton-primary"] {
    padding: .3rem .7rem !important; min-height: 0 !important;
}
/* alert / info / success boxes: tighter */
[data-testid="stAlert"], [data-testid="stAlertContainer"] { padding: .4rem .6rem !important; }
[data-testid="stAlert"] p { margin: 0 !important; font-size: .8rem !important; }
/* number inputs a touch shorter */
[data-testid="stNumberInput"] input { padding-top: .2rem !important; padding-bottom: .2rem !important; }
/* tabs: tighter tab bar */
[data-testid="stTabs"] [data-baseweb="tab"] { padding: .3rem .7rem !important; }
[data-testid="stTabs"] [data-baseweb="tab-list"] { gap: .2rem !important; }
/* sidebar padding */
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: .35rem !important; }
[data-testid="stSidebar"] .block-container { padding-top: 1rem !important; }
</style>
""", unsafe_allow_html=True)

ZONE_COLORS = {
    "600": "#4f6ef7",
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

import io
from datetime import datetime
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

AREA_COLS = [
    ("area_id",              "Area ID",             "Unique key - do not change"),
    ("area_name",            "Area Name",           "Display name - safe to edit"),
    ("zone",                 "Zone",                "Zone code - do not change"),
    ("rack_length_cuft",     "Rack Length (cu ft)", "Length of one rack in cubic feet"),
    ("rack_depth_cuft",      "Rack Depth (cu ft)",  "Depth of one rack in cubic feet"),
    ("rack_height_cuft",     "Rack Height (cu ft)", "Height of one rack in cubic feet"),
    ("num_racks",            "Number of Racks",     "How many racks in this area"),
    ("box_length_cuft",      "Box Length (cu ft)",  "Length of average box in cubic feet"),
    ("box_depth_cuft",       "Box Width (cu ft)",   "Width of average box in cubic feet"),
    ("box_height_cuft",      "Box Height (cu ft)",  "Height of average box in cubic feet"),
    ("efficiency",           "Efficiency (0-1)",    "Usable fraction, e.g. 0.75 = 75% after aisles"),
    ("units_per_box",        "Units per Box",       "Average units that fit in one box in this area"),
    ("max_concurrent_boxes", "Max Concurrent Boxes","Hard cap on boxes at once - leave blank for no cap"),
]

ORDER_COLS = [
    ("order_id",            "Order ID",            "Unique key - do not change"),
    ("order_name",          "Order Name",          "Display name - safe to edit"),
    ("daily_volume",        "Daily Volume",        "Number of orders per day"),
    ("avg_units_per_order", "Avg Units / Order",   "Average units per order"),
    ("paper_pct",           "Paper % (600)",       "Must total 100 with Consumable %"),
    ("consumable_pct",      "Consumable % (400)",  "Must total 100 with Paper %"),
    ("cust1_pct",           "Cust 1 % (Zone 300)", "Must total 100 with Cust 2 %"),
    ("cust2_pct",           "Cust 2 % (Zone 200)", "Must total 100 with Cust 1 %"),
    ("packout_pct",         "Direct to Packout %", "Must total 100 with Kitting %"),
    ("kitting_pct",         "Kitting %",           "Must total 100 with Direct to Packout %"),
]

HEADER_FILL  = PatternFill("solid", start_color="1F2937", end_color="1F2937")
HEADER_FONT  = Font(color="FFFFFF", bold=True, size=11, name="Calibri")
SUBLABEL_FONT= Font(color="6B7280", italic=True, size=9, name="Calibri")
TITLE_FONT   = Font(bold=True, size=16, name="Calibri", color="1F2937")
SECTION_FONT = Font(bold=True, size=12, name="Calibri", color="4F6EF7")
BODY_FONT    = Font(size=11, name="Calibri")
KEY_FILL     = PatternFill("solid", start_color="F3F4F6", end_color="F3F4F6")
THIN_BORDER  = Border(*[Side(style="thin", color="D1D5DB")] * 4)


def config_to_excel_bytes(areas, order_types) -> bytes:
    """Build a formatted .xlsx template: Instructions, Areas, Order Types sheets."""
    wb = Workbook()

    # ── Instructions sheet ───────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Instructions"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 95

    ws["A1"] = "WAREHOUSE CAPACITY PLANNER — Configuration Template"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Fill in the Areas and Order Types sheets, save the file, then upload it in the app's Settings page."
    ws["A2"].font = BODY_FONT
    r = 4
    for line in [
        "HOW THIS FILE IS ORGANIZED",
        "  •  Areas sheet        — physical dimensions for each storage area",
        "  •  Order Types sheet  — daily volume and percentage splits for each order type",
        "",
        "RULES",
        "  •  Do not rename Area ID or Order ID values — they are used as keys",
        "  •  Do not add or remove columns",
        "  •  Paper % + Consumable % must total 100 for every order type",
        "  •  Cust 1 % + Cust 2 % must total 100 for every order type",
        "  •  Direct to Packout % + Kitting % must total 100 for every order type",
        "  •  Max Concurrent Boxes: leave blank if there is no hard cap",
        "",
        "TIP",
        "  •  Download this template anytime from Settings to get your current live values pre-filled.",
    ]:
        ws.cell(row=r, column=1, value=line).font = (
            SECTION_FONT if line and line == line.upper() and not line.startswith(" ") else BODY_FONT
        )
        r += 1

    # ── Areas sheet ───────────────────────────────────────────────────────────
    ws_a = wb.create_sheet("Areas")
    ws_a.sheet_view.showGridLines = False
    for col_idx, (_, label, sub) in enumerate(AREA_COLS, start=1):
        c = ws_a.cell(row=1, column=col_idx, value=label)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = THIN_BORDER
        sub_c = ws_a.cell(row=2, column=col_idx, value=sub)
        sub_c.font = SUBLABEL_FONT
        sub_c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws_a.column_dimensions[get_column_letter(col_idx)].width = 22
    ws_a.row_dimensions[1].height = 32
    ws_a.row_dimensions[2].height = 28
    ws_a.freeze_panes = "A3"

    for r_idx, a in enumerate(areas, start=3):
        values = [
            a.id, a.name, a.zone,
            a.rack_length_cuft, a.rack_depth_cuft, a.rack_height_cuft, a.num_racks,
            a.box_length_cuft, a.box_depth_cuft, a.box_height_cuft,
            a.efficiency, a.units_per_box,
            a.max_concurrent_boxes if a.max_concurrent_boxes is not None else None,
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws_a.cell(row=r_idx, column=col_idx, value=val)
            cell.font = BODY_FONT
            cell.border = THIN_BORDER
            if col_idx == 1:
                cell.fill = KEY_FILL

    # ── Order Types sheet ────────────────────────────────────────────────────
    ws_o = wb.create_sheet("Order Types")
    ws_o.sheet_view.showGridLines = False
    for col_idx, (_, label, sub) in enumerate(ORDER_COLS, start=1):
        c = ws_o.cell(row=1, column=col_idx, value=label)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = THIN_BORDER
        sub_c = ws_o.cell(row=2, column=col_idx, value=sub)
        sub_c.font = SUBLABEL_FONT
        sub_c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws_o.column_dimensions[get_column_letter(col_idx)].width = 20
    ws_o.row_dimensions[1].height = 32
    ws_o.row_dimensions[2].height = 28
    ws_o.freeze_panes = "A3"

    for r_idx, ot in enumerate(order_types, start=3):
        values = [
            ot.id, ot.name, ot.daily_volume, ot.avg_units_per_order,
            ot.storage_split.paper_pct, ot.storage_split.consumable_pct,
            ot.customer_split.cust1_pct, ot.customer_split.cust2_pct,
            ot.kitting_split.packout_pct, ot.kitting_split.kitting_pct,
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws_o.cell(row=r_idx, column=col_idx, value=val)
            cell.font = BODY_FONT
            cell.border = THIN_BORDER
            if col_idx == 1:
                cell.fill = KEY_FILL

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def excel_bytes_to_config(file_bytes):
    """Parse the Areas and Order Types sheets from an uploaded .xlsx template."""
    wb = load_workbook(io.BytesIO(file_bytes), data_only=True)

    if "Areas" not in wb.sheetnames:
        raise ValueError("Sheet 'Areas' not found. Make sure you uploaded the correct template file.")
    if "Order Types" not in wb.sheetnames:
        raise ValueError("Sheet 'Order Types' not found. Make sure you uploaded the correct template file.")

    ws_a = wb["Areas"]
    ws_o = wb["Order Types"]
    area_keys  = [k for k, _, _ in AREA_COLS]
    order_keys = [k for k, _, _ in ORDER_COLS]

    areas = []
    for row in ws_a.iter_rows(min_row=3, values_only=True):
        if row[0] is None or str(row[0]).strip() == "":
            continue
        d = dict(zip(area_keys, row))
        max_b = d.get("max_concurrent_boxes")
        max_b = int(float(max_b)) if max_b not in (None, "") else None
        areas.append(StorageArea(
            id=str(d["area_id"]).strip(),
            name=str(d["area_name"]).strip(),
            zone=str(d["zone"]).strip(),
            rack_length_cuft=float(d["rack_length_cuft"]),
            rack_depth_cuft=float(d["rack_depth_cuft"]),
            rack_height_cuft=float(d["rack_height_cuft"]),
            num_racks=int(float(d["num_racks"])),
            box_length_cuft=float(d["box_length_cuft"]),
            box_depth_cuft=float(d["box_depth_cuft"]),
            box_height_cuft=float(d["box_height_cuft"]),
            efficiency=float(d["efficiency"]),
            units_per_box=float(d["units_per_box"]),
            max_concurrent_boxes=max_b,
        ))

    order_types = []
    for row in ws_o.iter_rows(min_row=3, values_only=True):
        if row[0] is None or str(row[0]).strip() == "":
            continue
        d = dict(zip(order_keys, row))
        order_types.append(OrderType(
            id=str(d["order_id"]).strip(),
            name=str(d["order_name"]).strip(),
            daily_volume=int(float(d["daily_volume"])),
            avg_units_per_order=int(float(d["avg_units_per_order"])),
            storage_split=StorageSplit(
                paper_pct=float(d["paper_pct"]),
                consumable_pct=float(d["consumable_pct"])),
            customer_split=CustomerSplit(
                cust1_pct=float(d["cust1_pct"]),
                cust2_pct=float(d["cust2_pct"])),
            kitting_split=KittingSplit(
                packout_pct=float(d["packout_pct"]),
                kitting_pct=float(d["kitting_pct"])),
        ))

    if not areas:
        raise ValueError("No area rows found in the 'Areas' sheet (starting row 3).")
    if not order_types:
        raise ValueError("No order rows found in the 'Order Types' sheet (starting row 3).")

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
    for zone, name in [("600","Paper"),("400","Consumables"),
                        ("300","Cust. Spec 1"),("200","Cust. Spec 2"),("100","Final")]:
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
        """
        snap = engine.snapshot(1.0)
        fl   = snap.flow
        fig  = go.Figure()

        # ── coordinate system: 0-100 x, 0-100 y ─────────────────────────
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
            import math
            dx, dy = x1-x0, y1-y0
            length = math.sqrt(dx*dx + dy*dy) or 1
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
            fig.add_shape(type="rect",
                x0=x-w/2+0.4, y0=y-h/2-0.4,
                x1=x+w/2+0.4, y1=y+h/2-0.4,
                xref="x", yref="y",
                fillcolor="rgba(0,0,0,0.4)", line=dict(width=0))
            fig.add_shape(type="rect",
                x0=x-w/2, y0=y-h/2,
                x1=x+w/2, y1=y+h/2,
                xref="x", yref="y",
                fillcolor=bg,
                line=dict(color=bc, width=2))
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
        add_arrow(50, 87, 22, 73.5, "#4f6ef7")
        add_arrow(50, 87, 78, 73.5, "#06b6d4")
        add_arrow(22, 66.5, 22, 53.5, "#7c5cfc")
        add_arrow(11, 50, 11, 10, "#7c5cfc", dash="dot", width=1.5)
        add_arrow(11, 10, 38, 10, "#7c5cfc", dash="dot", width=1.5)
        add_arrow(65, 70, 56, 53.5, "#10b981")
        add_arrow(78, 66.5, 78, 53.5, "#f59e0b")
        add_arrow(48, 46.5, 48, 33.5, "#10b981")
        add_arrow(78, 46.5, 78, 33.5, "#f59e0b")
        add_arrow(40, 30, 40, 50, "#6b7280", dash="dash", width=1.5)
        add_arrow(86, 30, 86, 50, "#6b7280", dash="dash", width=1.5)
        add_arrow(44, 46.5, 44, 13.5, "#10b981")
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
            text="<b>Today at x1.0:</b>  "
                 + "Paper->300: " + str(int(fl.paper_to_300)) + "  |  "
                 + "Paper->200: " + str(int(fl.paper_to_200)) + "  |  "
                 + "Cons->300: " + str(int(fl.consumables_to_300)) + "  |  "
                 + "Cons->200: " + str(int(fl.consumables_to_200)) + " boxes/day",
            showarrow=False, font=dict(size=11, color="#94a3b8"),
            xref="x", yref="y", align="center",
            bgcolor="rgba(30,34,53,0.9)", borderpad=6,
            bordercolor="#2e3250", borderwidth=1)

        fig.update_layout(
            height=540,
            margin=dict(l=10, r=10, t=20, b=10),
            paper_bgcolor="#0f1117",
            plot_bgcolor="#0f1117",
            xaxis=dict(visible=False, range=[0, 100], fixedrange=True),
            yaxis=dict(visible=False, range=[0, 100], fixedrange=True),
            showlegend=False,
        )
        return fig

    st.plotly_chart(make_flow_diagram(get_engine()), width="stretch")
    st.markdown("---")

    engine = get_engine()
    snap   = engine.snapshot(1.0)
    fl     = snap.flow

    st.subheader("Live flow volumes at x1.0")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Paper to Zone 300",       str(int(fl.paper_to_300))         + " boxes/day")
    c2.metric("Paper to Zone 200",       str(int(fl.paper_to_200))         + " boxes/day")
    c3.metric("Consumables to Zn 300",   str(int(fl.consumables_to_300))   + " boxes/day")
    c4.metric("Consumables to Zn 200",   str(int(fl.consumables_to_200))   + " boxes/day")

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
#  SETTINGS PAGE  — section renderers (one per sub-tab)
# ═══════════════════════════════════════════════════════════════════════════════

def _render_unit_converter():
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


def _render_area_settings():
    """Editable grid of ALL storage areas — one row each, no vertical scrolling."""
    st.subheader("Storage areas")
    ul = unit_label()
    st.caption(
        "Every area is one row — edit any cell directly. Rack and box dimensions are in "
        + ul + ". Capacity updates live in the preview below; changes apply when you hit Save."
    )

    df = pd.DataFrame([{
        "ID":        a.id,
        "Area":      a.name,
        "Zone":      a.zone,
        "Rack L":    round(to_display(a.rack_length_cuft), 2),
        "Rack D":    round(to_display(a.rack_depth_cuft), 2),
        "Rack H":    round(to_display(a.rack_height_cuft), 2),
        "# Racks":   int(a.num_racks),
        "Box L":     round(to_display(a.box_length_cuft), 3),
        "Box W":     round(to_display(a.box_depth_cuft), 3),
        "Box H":     round(to_display(a.box_height_cuft), 3),
        "Eff.":      float(a.efficiency),
        "Units/box": float(a.units_per_box),
        "Max boxes": int(a.max_concurrent_boxes) if a.max_concurrent_boxes is not None else 0,
    } for a in st.session_state.areas]).set_index("ID")

    row_h    = 29
    grid_h   = int((len(df) + 1) * row_h + 3)
    edited = st.data_editor(
        df, key="areas_editor", hide_index=True, num_rows="fixed",
        width="stretch", height=grid_h, row_height=row_h,
        column_config={
            "Area":      st.column_config.TextColumn("Area", width="medium"),
            "Zone":      st.column_config.TextColumn("Zone", disabled=True, width="small"),
            "Rack L":    st.column_config.NumberColumn("Rack L (" + ul + ")", min_value=0.001, step=0.1,  format="%.2f"),
            "Rack D":    st.column_config.NumberColumn("Rack D (" + ul + ")", min_value=0.001, step=0.1,  format="%.2f"),
            "Rack H":    st.column_config.NumberColumn("Rack H (" + ul + ")", min_value=0.001, step=0.1,  format="%.2f"),
            "# Racks":   st.column_config.NumberColumn("# Racks",  min_value=1,     step=1,    format="%d"),
            "Box L":     st.column_config.NumberColumn("Box L (" + ul + ")", min_value=0.001, step=0.05, format="%.3f"),
            "Box W":     st.column_config.NumberColumn("Box W (" + ul + ")", min_value=0.001, step=0.05, format="%.3f"),
            "Box H":     st.column_config.NumberColumn("Box H (" + ul + ")", min_value=0.001, step=0.05, format="%.3f"),
            "Eff.":      st.column_config.NumberColumn("Eff. (0–1)", min_value=0.1, max_value=1.0, step=0.01, format="%.2f"),
            "Units/box": st.column_config.NumberColumn("Units/box", min_value=1.0, step=1.0, format="%.0f"),
            "Max boxes": st.column_config.NumberColumn("Max boxes", min_value=0, step=10, format="%d",
                                                       help="Optional hard cap. 0 = no cap (volume-based)."),
        },
    )

    # ── build updates + live capacity preview from the edited grid ────────────
    area_updates, prev_rows = {}, []
    for aid, row in edited.iterrows():
        rl, rd, rh = to_cuft(row["Rack L"]), to_cuft(row["Rack D"]), to_cuft(row["Rack H"])
        bl, bw, bh = to_cuft(row["Box L"]),  to_cuft(row["Box W"]),  to_cuft(row["Box H"])
        racks = int(row["# Racks"]); eff = float(row["Eff."]); upb = float(row["Units/box"])
        max_raw = int(row["Max boxes"]); max_b = max_raw if max_raw > 0 else None

        total_vol = rl * rd * rh * racks
        box_vol   = bl * bw * bh
        vol_cap   = int((total_vol * eff) / box_vol) if box_vol > 0 else 0
        cap       = min(max_b, vol_cap) if max_b else vol_cap

        area_updates[aid] = dict(
            name=str(row["Area"]),
            rack_length_cuft=rl, rack_depth_cuft=rd, rack_height_cuft=rh,
            num_racks=racks,
            box_length_cuft=bl, box_depth_cuft=bw, box_height_cuft=bh,
            efficiency=eff, units_per_box=upb, max_concurrent_boxes=max_b,
        )
        prev_rows.append({
            "Area":                 str(row["Area"]),
            "Volume (" + ul + ")":  round(to_display(total_vol), 1),
            "Capacity (boxes)":     cap,
            "Capacity (units)":     int(cap * upb),
            "Cap source":           "🔢 box cap" if (max_b and max_b < vol_cap) else "volume",
        })

    st.caption("**Live capacity preview** — recalculates as you type, applied on Save:")
    prev_df = pd.DataFrame(prev_rows)
    st.dataframe(prev_df, hide_index=True, width="stretch",
                 height=int((len(prev_df) + 1) * row_h + 3), row_height=row_h)
    return area_updates


def _render_order_settings():
    """Editable grid of ALL order types — one row each, with live split checks."""
    st.subheader("Order types")
    st.caption(
        "Every order type is one row — edit any cell directly. The three split pairs must "
        "each total 100% (600+400, 300+200, packout+kitting). Changes apply when you hit Save."
    )

    df = pd.DataFrame([{
        "ID":        ot.id,
        "Order":     ot.name,
        "Daily vol": int(ot.daily_volume),
        "Avg units": int(ot.avg_units_per_order),
        "600 %":     float(ot.storage_split.paper_pct),
        "400 %":     float(ot.storage_split.consumable_pct),
        "300 %":     float(ot.customer_split.cust1_pct),
        "200 %":     float(ot.customer_split.cust2_pct),
        "Packout %": float(ot.kitting_split.packout_pct),
        "Kitting %": float(ot.kitting_split.kitting_pct),
    } for ot in st.session_state.order_types]).set_index("ID")

    def pct(label):
        return st.column_config.NumberColumn(label, min_value=0.0, max_value=100.0, step=1.0, format="%.0f")

    row_h  = 29
    grid_h = int((len(df) + 1) * row_h + 3)
    edited = st.data_editor(
        df, key="orders_editor", hide_index=True, num_rows="fixed",
        width="stretch", height=grid_h, row_height=row_h,
        column_config={
            "Order":     st.column_config.TextColumn("Order", width="medium"),
            "Daily vol": st.column_config.NumberColumn("Daily vol", min_value=1, step=1, format="%d"),
            "Avg units": st.column_config.NumberColumn("Avg units", min_value=1, step=1, format="%d"),
            "600 %":     pct("600 Paper %"),
            "400 %":     pct("400 Cons %"),
            "300 %":     pct("300 Cust1 %"),
            "200 %":     pct("200 Cust2 %"),
            "Packout %": pct("Packout %"),
            "Kitting %": pct("Kitting %"),
        },
    )

    # ── build updates + live split validation from the edited grid ────────────
    order_updates, check_rows, all_ok = {}, [], True
    for oid, row in edited.iterrows():
        s1 = float(row["600 %"]) + float(row["400 %"])
        s2 = float(row["300 %"]) + float(row["200 %"])
        s3 = float(row["Packout %"]) + float(row["Kitting %"])
        ok = all(abs(s - 100) <= 0.5 for s in (s1, s2, s3))
        all_ok = all_ok and ok
        check_rows.append({
            "Order":           str(row["Order"]),
            "Storage 600+400": f"{s1:.0f}%",
            "Cust 300+200":    f"{s2:.0f}%",
            "Kit pack+kitting":f"{s3:.0f}%",
            "Units/day":       int(row["Daily vol"]) * int(row["Avg units"]),
            "Valid":           "✅" if ok else "⚠️",
        })
        order_updates[oid] = dict(
            name=str(row["Order"]),
            daily_volume=int(row["Daily vol"]),
            avg_units_per_order=int(row["Avg units"]),
            paper_pct=float(row["600 %"]),   consumable_pct=float(row["400 %"]),
            cust1_pct=float(row["300 %"]),   cust2_pct=float(row["200 %"]),
            packout_pct=float(row["Packout %"]), kitting_pct=float(row["Kitting %"]),
        )

    st.caption("**Split validation** — each pair must total 100%:")
    check_df = pd.DataFrame(check_rows)
    st.dataframe(check_df, hide_index=True, width="stretch",
                 height=int((len(check_df) + 1) * row_h + 3), row_height=row_h)
    if all_ok:
        st.success("All split pairs total 100%.")
    else:
        st.warning("Some split pairs don't total 100% (see ⚠️ rows) — fix before saving for accurate results.")
    return order_updates


def _apply_settings(area_updates, order_updates):
    """Write the collected edits back into session state (and DB if configured)."""
    area_map = {a.id: a for a in st.session_state.areas}
    for aid, u in area_updates.items():
        a = area_map[aid]
        a.name                = u.get("name", a.name)
        a.rack_length_cuft    = u["rack_length_cuft"]
        a.rack_depth_cuft     = u["rack_depth_cuft"]
        a.rack_height_cuft    = u["rack_height_cuft"]
        a.num_racks           = u["num_racks"]
        a.box_length_cuft     = u["box_length_cuft"]
        a.box_depth_cuft      = u["box_depth_cuft"]
        a.box_height_cuft     = u["box_height_cuft"]
        a.efficiency          = u["efficiency"]
        a.units_per_box       = u["units_per_box"]
        a.max_concurrent_boxes = u["max_concurrent_boxes"]

    ot_map = {o.id: o for o in st.session_state.order_types}
    for oid, u in order_updates.items():
        ot = ot_map[oid]
        ot.name                = u.get("name", ot.name)
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


def _render_excel_io():
    st.subheader("💾 Save / Load configuration (Excel)")
    st.caption(
        "Download the template, fill it in Excel, then upload it here to apply your values."
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    excel_data = config_to_excel_bytes(st.session_state.areas, st.session_state.order_types)

    col_dl, col_ul = st.columns(2)

    with col_dl:
        st.markdown("**Step 1 — Download template**")
        st.caption(
            "Contains your current values pre-filled across three sheets: "
            "Instructions, Areas, and Order Types. Edit numbers directly, save, and upload."
        )
        st.download_button(
            "⬇️ Download configuration template (.xlsx)",
            data=excel_data,
            file_name="warehouse_config_" + timestamp + ".xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )

    with col_ul:
        st.markdown("**Step 2 — Upload filled template**")
        st.caption("Upload the same .xlsx after editing. Both sheets load at once.")
        uploaded = st.file_uploader(
            "Choose Excel file", type=["xlsx"], key="upload_config",
            label_visibility="collapsed")

        if uploaded is not None:
            try:
                preview_areas, preview_orders = excel_bytes_to_config(uploaded.getvalue())
                st.success(
                    "✅ File parsed successfully — " + str(len(preview_areas)) + " areas and "
                    + str(len(preview_orders)) + " order types found. Review below, then click Apply.")

                with st.expander("📋 Preview imported values", expanded=True):
                    st.markdown("**Areas**")
                    st.dataframe(
                        pd.DataFrame([{
                            "Area": a.name, "Zone": a.zone,
                            "L×D×H (cu ft)": f"{a.rack_length_cuft}×{a.rack_depth_cuft}×{a.rack_height_cuft}",
                            "Racks": a.num_racks,
                            "Volume (cu ft)": round(a.volume_cuft, 1),
                            "Box L×W×H (cu ft)": str(a.box_length_cuft)+"×"+str(a.box_depth_cuft)+"×"+str(a.box_height_cuft),
                            "Efficiency": a.efficiency,
                            "Units/box": a.units_per_box,
                            "Max boxes": a.max_concurrent_boxes or "—",
                        } for a in preview_areas]),
                        width="stretch", hide_index=True,
                        height=int((len(preview_areas) + 1) * 29 + 3), row_height=29)
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
                        width="stretch", hide_index=True,
                        height=int((len(preview_orders) + 1) * 29 + 3), row_height=29)

                if st.button("✅ Apply imported configuration", type="primary", width="stretch", key="apply_excel_config"):
                    st.session_state.areas = preview_areas
                    st.session_state.order_types = preview_orders
                    if db.is_db_configured():
                        db.save_all(preview_areas, preview_orders)
                        st.success("Configuration loaded and saved to database.")
                    else:
                        st.success("Configuration loaded (session only — no database configured).")
                    st.rerun()

            except Exception as e:
                st.error("❌ Could not parse Excel file: " + str(e))
                st.caption(
                    "Common causes: the 'Areas' or 'Order Types' sheet was renamed/removed, "
                    "a required column header was changed, or a percentage cell has text instead of a number."
                )
                with st.expander("🔍 Sheet names found in uploaded file"):
                    try:
                        from openpyxl import load_workbook as _lw
                        wb_debug = _lw(io.BytesIO(uploaded.getvalue()))
                        st.write(wb_debug.sheetnames)
                    except Exception:
                        st.caption("Could not read the uploaded file at all — it may not be a valid .xlsx file.")


def _render_db_controls():
    st.subheader("🗄️ Database controls")
    if db.is_db_configured():
        st.success("Database connected — Save & recalculate also writes here automatically.")
        dbc1, dbc2 = st.columns(2)
        with dbc1:
            if st.button("🔄 Reload from database", width="stretch"):
                _areas, _orders = db.load_all()
                st.session_state.areas = _areas
                st.session_state.order_types = _orders
                st.success("Reloaded from database.")
                st.rerun()
        with dbc2:
            if st.button("↩️ Reset to factory defaults", width="stretch"):
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
        with st.expander("🔍 Run connection diagnostic", expanded=True):
            diag = db.diagnose()
            steps = [
                ("Secrets section [supabase] found", diag["secrets_section_found"]),
                ("url value found",                  diag["url_found"]),
                ("key value found",                  diag["key_found"]),
                ("Client created",                   diag["client_created"]),
                ("Test query to database succeeded", diag["query_succeeded"]),
            ]
            for label, ok in steps:
                if ok:
                    st.markdown("✅ " + label)
                else:
                    st.markdown("❌ " + label)
                    break
            if diag["url_preview"]:
                st.caption("URL detected: " + diag["url_preview"])
            if diag["error"]:
                st.error(diag["error"])
            st.caption(
                "Run this after every change to Secrets — Streamlit Cloud needs "
                "~20-30 seconds and sometimes a manual reboot (⋮ menu → Reboot app) "
                "to pick up new secrets."
            )


if page == "⚙️ Settings":
    st.title("⚙️ Settings")
    st.caption("Each section is its own tab — pick one below, no more scrolling. Edits apply when you hit Save.")

    tab_areas, tab_orders, tab_io, tab_db, tab_conv = st.tabs([
        "🗺️ Storage Areas",
        "📦 Order Types",
        "💾 Import / Export",
        "🗄️ Database",
        "🔁 Unit Converter",
    ])

    area_updates, order_updates = {}, {}
    save_from_areas = save_from_orders = False

    with tab_areas:
        area_updates = _render_area_settings()
        st.markdown("---")
        save_from_areas = st.button(
            "💾 Save & recalculate", type="primary",
            width="stretch", key="save_areas_btn")

    with tab_orders:
        order_updates = _render_order_settings()
        st.markdown("---")
        save_from_orders = st.button(
            "💾 Save & recalculate", type="primary",
            width="stretch", key="save_orders_btn")

    with tab_io:
        _render_excel_io()

    with tab_db:
        _render_db_controls()

    with tab_conv:
        _render_unit_converter()

    # Either Save button applies edits from BOTH editing tabs (they both render
    # every run, so both update dicts are always populated).
    if save_from_areas or save_from_orders:
        _apply_settings(area_updates, order_updates)
        st.rerun()


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
            height=250, margin=dict(l=10, r=60, t=20, b=40),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch")

        rows = []
        for a in snap.areas:
            cap_src = "box cap" if a.area.has_box_cap else "volume"
            rows.append({
                "Area":         a.area.name,
                "Zone":         a.area.zone,
                "Box L×W×H": str(a.area.box_length_cuft)+"×"+str(a.area.box_depth_cuft)+"×"+str(a.area.box_height_cuft),
                "Box vol (cu ft)": round(a.area.avg_box_size_cuft,3),
                "Units/box":    int(a.area.units_per_box),
                "Load (boxes)": str(int(a.load_boxes)),
                "Cap (boxes)":  str(a.capacity_boxes) + (" 🔢" if a.area.has_box_cap else ""),
                "Load (units)": str(int(a.load_units)),
                "Cap (units)":  str(a.capacity_units),
                "Cap source":   cap_src,
                "Util %":       str(round(a.utilization_pct, 1)) + "%",
                "Status":       status_label(a.utilization_pct),
            })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
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
                    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
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
        fig3.update_layout(height=360, margin=dict(l=10,r=10,t=20,b=10),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(tickangle=-30))
        st.plotly_chart(fig3, width="stretch")

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
                     width="stretch", hide_index=True)

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
                width="stretch", hide_index=True)
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
                height=220, margin=dict(l=10,r=60,t=40,b=40),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                barmode="overlay")
            st.plotly_chart(fig4, width="stretch")
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
            fig5.update_layout(height=250, margin=dict(l=10,r=10,t=40,b=40),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig5, width="stretch")

            disp = bom_df[["order_name","zone_name","pct_of_total","units","units_per_box","boxes"]].copy()
            disp.columns = ["Order","Zone","% of total","Units/day","Units per box","Boxes/day"]
            st.dataframe(disp, width="stretch", hide_index=True)
