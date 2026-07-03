"""
Warehouse Capacity Planner — Terminal Reports & CLI
Run:  python main.py
"""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich import box
from rich.rule import Rule
import sys

from config import DEFAULT_AREAS, DEFAULT_ORDER_TYPES, ZONE_NAMES, ZONE_FLOW_ORDER
from engine import WarehouseEngine, WarehouseSnapshot, AreaSnapshot

console = Console()


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def status_color(status: str) -> str:
    return {
        "OK": "green",
        "WARNING": "yellow",
        "CRITICAL": "dark_orange",
        "OVER CAPACITY": "red bold",
    }.get(status, "white")


def util_bar(pct: float, width: int = 20) -> str:
    filled = min(int(pct / 100 * width), width)
    bar = "█" * filled + "░" * (width - filled)
    color = "green" if pct < 70 else ("yellow" if pct < 85 else ("dark_orange" if pct < 100 else "red"))
    return f"[{color}]{bar}[/{color}] {pct:.1f}%"


# ---------------------------------------------------------------------------
# Report: current snapshot
# ---------------------------------------------------------------------------

def report_snapshot(snap: WarehouseSnapshot, title: str = "Current state"):
    console.print(Rule(f"[bold]{title}[/bold]  (×{snap.multiplier:.2f} volume)", style="blue"))

    # Summary metrics
    cols = [
        Panel(f"[bold]{snap.total_capacity_boxes:,}[/bold]\nboxes", title="Total capacity", border_style="dim"),
        Panel(f"[bold]{snap.total_load_boxes:,.0f}[/bold]\nboxes", title="Current load", border_style="dim"),
        Panel(
            Text(f"{snap.overall_utilization:.1f}%", style="bold " + (
                "red" if snap.overall_utilization >= 100 else
                "yellow" if snap.overall_utilization >= 80 else "green"
            )),
            title="Overall util.", border_style="dim",
        ),
        Panel(
            Text(str(len(snap.bottlenecks)) + "\narea(s)", style="bold " + ("red" if snap.bottlenecks else "green")),
            title="Over capacity", border_style="dim",
        ),
        Panel(
            Text(str(len(snap.warnings)) + "\narea(s)", style="bold " + ("yellow" if snap.warnings else "green")),
            title="Near limit (85–100%)", border_style="dim",
        ),
    ]
    console.print(Columns(cols))
    console.print()

    # Area utilization table
    tbl = Table(box=box.SIMPLE_HEAVY, show_footer=False, pad_edge=False)
    tbl.add_column("Area", style="bold", min_width=12)
    tbl.add_column("Zones", style="dim", min_width=10)
    tbl.add_column("Load", justify="right", min_width=8)
    tbl.add_column("Capacity", justify="right", min_width=8)
    tbl.add_column("Utilization", min_width=32)
    tbl.add_column("Status", min_width=14)

    for a in snap.areas:
        sc = status_color(a.status)
        tbl.add_row(
            a.area.name,
            a.area.zone,
            f"{a.load_boxes:,.0f}",
            f"{a.capacity_boxes:,}",
            util_bar(a.utilization_pct),
            f"[{sc}]{a.status}[/{sc}]",
        )

    console.print(tbl)


# ---------------------------------------------------------------------------
# Report: zone summary
# ---------------------------------------------------------------------------

def report_zones(engine: WarehouseEngine, multiplier: float = 1.0):
    console.print(Rule("[bold]Zone-level summary[/bold]", style="blue"))
    df = engine.zone_summary(multiplier)

    tbl = Table(box=box.SIMPLE_HEAVY, pad_edge=False)
    tbl.add_column("Code", style="bold", min_width=6)
    tbl.add_column("Zone name", min_width=18)
    tbl.add_column("Areas", style="dim", min_width=28)
    tbl.add_column("Capacity", justify="right")
    tbl.add_column("Load", justify="right")
    tbl.add_column("Utilization", min_width=28)

    for _, row in df.iterrows():
        pct = row["utilization_pct"]
        tbl.add_row(
            row["zone_code"],
            row["zone_name"],
            row["areas"],
            f"{int(row['capacity_boxes']):,}",
            f"{row['load_boxes']:,.0f}",
            util_bar(pct, width=16),
        )

    console.print(tbl)
    console.print()


# ---------------------------------------------------------------------------
# Report: bottleneck sequence
# ---------------------------------------------------------------------------

def report_bottleneck_sequence(engine: WarehouseEngine, max_mult: float = 15.0):
    console.print(Rule("[bold]Bottleneck sequence[/bold] — order areas will hit capacity", style="red"))
    seq = engine.bottleneck_sequence(threshold_pct=100.0, max_mult=max_mult)

    if not seq:
        console.print(f"[green]No areas hit capacity up to ×{max_mult:.0f} volume.[/green]\n")
        return

    tbl = Table(box=box.SIMPLE_HEAVY, pad_edge=False)
    tbl.add_column("Order", justify="center", style="bold", min_width=6)
    tbl.add_column("At multiplier", justify="center", min_width=14)
    tbl.add_column("Area", min_width=14)
    tbl.add_column("Note", style="dim")

    for rank, (mult, area_name, status) in enumerate(seq, 1):
        note = "First bottleneck — address this first" if rank == 1 else ""
        color = "red" if rank == 1 else "dark_orange" if rank == 2 else "yellow"
        tbl.add_row(
            f"[{color}]#{rank}[/{color}]",
            f"[{color}]×{mult:.1f}[/{color}]",
            area_name,
            note,
        )

    console.print(tbl)
    console.print()


# ---------------------------------------------------------------------------
# Report: growth table (wide)
# ---------------------------------------------------------------------------

def report_growth_table(engine: WarehouseEngine, max_mult: float = 3.0, steps: int = 10):
    console.print(Rule(f"[bold]Growth table[/bold] — utilization % from ×1 to ×{max_mult:.0f}", style="blue"))
    df = engine.growth_table(max_multiplier=max_mult, steps=steps)

    pivot = df.pivot_table(
        index="multiplier",
        columns="area",
        values="utilization_pct",
        aggfunc="first",
    ).reset_index()

    area_names = [a.name for a in engine.areas]

    tbl = Table(box=box.SIMPLE_HEAVY, pad_edge=False)
    tbl.add_column("Mult.", justify="center", min_width=6)
    for name in area_names:
        tbl.add_column(name, justify="right", min_width=10)

    for _, row in pivot.iterrows():
        cells = [f"×{row['multiplier']:.1f}"]
        for name in area_names:
            val = row.get(name, 0)
            color = "green" if val < 70 else ("yellow" if val < 85 else ("dark_orange" if val < 100 else "red bold"))
            cells.append(f"[{color}]{val:.0f}%[/{color}]")
        tbl.add_row(*cells)

    console.print(tbl)
    console.print()


# ---------------------------------------------------------------------------
# Report: order type impact
# ---------------------------------------------------------------------------

def report_order_impact(engine: WarehouseEngine, order_id: str, max_mult: float = 3.0):
    ot = next((o for o in engine.order_types if o.id == order_id), None)
    if not ot:
        console.print(f"[red]Order type '{order_id}' not found.[/red]")
        return

    console.print(Rule(f"[bold]Order impact: {ot.name}[/bold]", style="blue"))
    df = engine.order_impact(order_id=order_id, max_multiplier=max_mult, steps=10)

    if df.empty:
        console.print("[dim]No area interactions found for this order type.[/dim]\n")
        return

    tbl = Table(box=box.SIMPLE_HEAVY, pad_edge=False)
    tbl.add_column("Mult.", justify="center", min_width=6)
    tbl.add_column("Area", min_width=12)
    tbl.add_column("Boxes from this OT", justify="right", min_width=18)
    tbl.add_column("Total load", justify="right", min_width=10)
    tbl.add_column("Capacity", justify="right", min_width=10)
    tbl.add_column("Util %", justify="right", min_width=8)
    tbl.add_column("Status")

    for _, row in df.iterrows():
        sc = status_color(row["status"])
        tbl.add_row(
            f"×{row['order_multiplier']:.1f}",
            row["area"],
            f"{row['load_from_order']:,.0f}",
            f"{row['total_load']:,.0f}",
            f"{row['capacity']:,}",
            f"{row['utilization_pct']:.1f}%",
            f"[{sc}]{row['status']}[/{sc}]",
        )

    console.print(tbl)
    console.print()


# ---------------------------------------------------------------------------
# Report: capacity setup
# ---------------------------------------------------------------------------

def report_capacity_setup(engine: WarehouseEngine):
    console.print(Rule("[bold]Area capacity configuration[/bold]", style="blue"))
    df = engine.capacity_summary()

    tbl = Table(box=box.SIMPLE_HEAVY, pad_edge=False)
    tbl.add_column("Area", style="bold", min_width=12)
    tbl.add_column("Zones", style="dim", min_width=10)
    tbl.add_column("Volume (cu ft)", justify="right", min_width=14)
    tbl.add_column("Avg box (cu ft)", justify="right", min_width=14)
    tbl.add_column("Efficiency", justify="right", min_width=10)
    tbl.add_column("Capacity (boxes)", justify="right", min_width=16)

    for _, row in df.iterrows():
        tbl.add_row(
            row["area"],
            row["zone"],
            f"{row['volume_cuft']:,.0f}",
            f"{row['avg_box_size_cuft']:.1f}",
            f"{row['efficiency']*100:.0f}%",
            f"{row['capacity_boxes']:,}",
        )

    console.print(tbl)
    console.print()


# ---------------------------------------------------------------------------
# Report: order type configuration
# ---------------------------------------------------------------------------

def report_order_types(engine: WarehouseEngine):
    console.print(Rule("[bold]Order type configuration[/bold]", style="blue"))

    tbl = Table(box=box.SIMPLE_HEAVY, pad_edge=False)
    tbl.add_column("ID", style="bold", min_width=5)
    tbl.add_column("Name", min_width=22)
    tbl.add_column("Daily vol.", justify="right", min_width=10)
    tbl.add_column("Avg units/order", justify="right", min_width=15)
    tbl.add_column("Split 1\n600 / 400", justify="center", min_width=11)
    tbl.add_column("Split 2\n300 / 200", justify="center", min_width=11)
    tbl.add_column("Split 3\npackout / kit", justify="center", min_width=13)

    for ot in engine.order_types:
        tbl.add_row(
            ot.id, ot.name,
            str(ot.daily_volume),
            str(ot.avg_units_per_order),
            f"{ot.storage_split.paper_pct:.0f} / {ot.storage_split.consumable_pct:.0f}",
            f"{ot.customer_split.cust1_pct:.0f} / {ot.customer_split.cust2_pct:.0f}",
            f"{ot.kitting_split.packout_pct:.0f} / {ot.kitting_split.kitting_pct:.0f}",
        )

    console.print(tbl)
    console.print()


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def main():
    engine = WarehouseEngine()
    snap_1x = engine.snapshot(multiplier=1.0)

    area_list  = " · ".join(a.name for a in engine.areas)
    order_list = " · ".join(o.id for o in engine.order_types)
    console.print()
    console.print(Panel(
        "[bold white]Warehouse Capacity Planner[/bold white]\n"
        "[dim]Areas: " + area_list + "[/dim]\n"
        "[dim]Orders: " + order_list + "[/dim]",
        border_style="blue",
        padding=(0, 2),
    ))
    console.print()

    # 1 — Capacity setup
    report_capacity_setup(engine)

    # 2 — Order type config
    report_order_types(engine)

    # 3 — Current snapshot (1×)
    report_snapshot(snap_1x, title="Baseline snapshot (×1.0 volume)")
    console.print()

    # 4 — Zone summary
    report_zones(engine, multiplier=1.0)

    # 5 — Growth table
    report_growth_table(engine, max_mult=5.0, steps=10)

    # 6 — Bottleneck sequence
    report_bottleneck_sequence(engine, max_mult=15.0)

    # 7 — SO order type deep-dive (primary order type)
    report_order_impact(engine, order_id="SO", max_mult=5.0)

    # 8 — Snapshot at 3×
    snap_3x = engine.snapshot(multiplier=3.0)
    report_snapshot(snap_3x, title="Projected state at ×3.0 volume")
    console.print()

    console.print(Rule("[dim]End of report[/dim]"))
    console.print()


if __name__ == "__main__":
    main()
