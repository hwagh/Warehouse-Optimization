"""
Warehouse Capacity Planner — Database persistence layer (Supabase)

Setup required (one-time):
  1. Create a free project at https://supabase.com
  2. In the SQL Editor, run the schema below (also in supabase_schema.sql)
  3. Get your Project URL and anon/public API key from
     Project Settings → API
  4. Add them to Streamlit secrets (see README for instructions):

       [supabase]
       url = "https://xxxx.supabase.co"
       key = "your-anon-public-key"

If secrets are not configured, the app falls back to session-only
storage (values reset on refresh) so it still works without a database.
"""

from __future__ import annotations
from typing import List, Optional
import streamlit as st

from config import (
    StorageArea, OrderType, StorageSplit, CustomerSplit, KittingSplit,
    DEFAULT_AREAS, DEFAULT_ORDER_TYPES,
)

# Default scenario name — single shared scenario for the internal demo.
# Could be extended later to support multiple named scenarios per user.
SCENARIO_NAME = "default"


def get_client():
    """Return a Supabase client, or None if not configured."""
    try:
        from supabase import create_client
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
        return create_client(url, key)
    except Exception:
        return None


def diagnose() -> dict:
    """
    Run through each step of the connection process and report exactly
    where it fails. Used by the Settings page to show a real error
    instead of a generic 'not connected' message.
    """
    result = {
        "secrets_section_found": False,
        "url_found": False,
        "key_found": False,
        "client_created": False,
        "query_succeeded": False,
        "error": None,
        "url_preview": None,
    }
    try:
        try:
            has_secrets = "supabase" in st.secrets
        except Exception:
            result["error"] = (
                "No secrets configured at all for this app. "
                "On Streamlit Cloud: open your app -> click the ⋮ menu (bottom right) "
                "-> Settings -> Secrets -> paste the [supabase] block -> Save -> "
                "wait ~30 sec, then click ⋮ -> Reboot app."
            )
            return result

        if not has_secrets:
            result["error"] = (
                "Secrets exist but no [supabase] section was found. "
                "Check Streamlit Cloud → Settings → Secrets has a section "
                "literally named [supabase] (lowercase, square brackets)."
            )
            return result
        result["secrets_section_found"] = True

        sec = st.secrets["supabase"]
        url = sec.get("url", None)
        key = sec.get("key", None)

        if not url:
            result["error"] = "Found [supabase] section but 'url' key is missing or empty."
            return result
        result["url_found"] = True
        result["url_preview"] = url[:30] + "..." if len(url) > 30 else url

        if not key:
            result["error"] = "Found [supabase] section but 'key' key is missing or empty."
            return result
        result["key_found"] = True

        from supabase import create_client
        client = create_client(url, key)
        result["client_created"] = True

        # Try an actual query to confirm the table exists and credentials work
        resp = client.table("warehouse_areas").select("*").limit(1).execute()
        result["query_succeeded"] = True

    except ImportError as e:
        result["error"] = "supabase package not installed: " + str(e) + ". Check requirements.txt includes 'supabase'."
    except Exception as e:
        msg = str(e)
        if "Invalid API key" in msg or "JWT" in msg:
            result["error"] = "Connected but the API key was rejected. Double-check you copied the 'anon public' key, not a truncated or wrong key."
        elif "relation" in msg.lower() and "does not exist" in msg.lower():
            result["error"] = "Connected successfully, but the table 'warehouse_areas' doesn't exist yet. Run supabase_schema.sql in the Supabase SQL Editor."
        elif "Name or service not known" in msg or "getaddrinfo" in msg:
            result["error"] = "Could not reach the URL — check the project URL is correct and has no typos."
        else:
            result["error"] = "Unexpected error: " + msg
    return result


def is_db_configured() -> bool:
    return get_client() is not None


# ── AREAS ───────────────────────────────────────────────────────────────────

def save_areas(areas: List[StorageArea]) -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        client.table("warehouse_areas").delete().eq("scenario", SCENARIO_NAME).execute()
        rows = []
        for a in areas:
            rows.append({
                "scenario":              SCENARIO_NAME,
                "area_id":               a.id,
                "name":                  a.name,
                "zone":                  a.zone,
                "rack_length_cuft":      a.rack_length_cuft,
                "rack_depth_cuft":       a.rack_depth_cuft,
                "rack_height_cuft":      a.rack_height_cuft,
                "num_racks":             a.num_racks,
                "box_length_cuft":       a.box_length_cuft,
                "box_depth_cuft":        a.box_depth_cuft,
                "box_height_cuft":       a.box_height_cuft,
                "efficiency":            a.efficiency,
                "units_per_box":         a.units_per_box,
                "max_concurrent_boxes":  a.max_concurrent_boxes,
            })
        client.table("warehouse_areas").insert(rows).execute()
        return True
    except Exception as e:
        st.error("Database save failed (areas): " + str(e))
        return False


def load_areas() -> Optional[List[StorageArea]]:
    client = get_client()
    if client is None:
        return None
    try:
        resp = client.table("warehouse_areas").select("*").eq("scenario", SCENARIO_NAME).execute()
        rows = resp.data
        if not rows:
            return None
        result = []
        for row in rows:
            result.append(StorageArea(
                id=row["area_id"], name=row["name"], zone=row["zone"],
                rack_length_cuft=float(row["rack_length_cuft"]),
                rack_depth_cuft=float(row["rack_depth_cuft"]),
                rack_height_cuft=float(row["rack_height_cuft"]),
                num_racks=int(row["num_racks"]),
                box_length_cuft=float(row["box_length_cuft"]),
                box_depth_cuft=float(row["box_depth_cuft"]),
                box_height_cuft=float(row["box_height_cuft"]),
                efficiency=float(row["efficiency"]),
                units_per_box=float(row["units_per_box"]),
                max_concurrent_boxes=(
                    int(row["max_concurrent_boxes"])
                    if row.get("max_concurrent_boxes") is not None else None
                ),
            ))
        return result
    except Exception as e:
        st.error("Database load failed (areas): " + str(e))
        return None


# ── ORDER TYPES ───────────────────────────────────────────────────────────────

def save_order_types(order_types: List[OrderType]) -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        client.table("warehouse_order_types").delete().eq("scenario", SCENARIO_NAME).execute()
        rows = []
        for ot in order_types:
            rows.append({
                "scenario":             SCENARIO_NAME,
                "order_id":             ot.id,
                "name":                 ot.name,
                "daily_volume":         ot.daily_volume,
                "avg_units_per_order":  ot.avg_units_per_order,
                "paper_pct":            ot.storage_split.paper_pct,
                "consumable_pct":       ot.storage_split.consumable_pct,
                "cust1_pct":            ot.customer_split.cust1_pct,
                "cust2_pct":            ot.customer_split.cust2_pct,
                "packout_pct":          ot.kitting_split.packout_pct,
                "kitting_pct":          ot.kitting_split.kitting_pct,
            })
        client.table("warehouse_order_types").insert(rows).execute()
        return True
    except Exception as e:
        st.error("Database save failed (order types): " + str(e))
        return False


def load_order_types() -> Optional[List[OrderType]]:
    client = get_client()
    if client is None:
        return None
    try:
        resp = client.table("warehouse_order_types").select("*").eq("scenario", SCENARIO_NAME).execute()
        rows = resp.data
        if not rows:
            return None
        result = []
        for row in rows:
            result.append(OrderType(
                id=row["order_id"], name=row["name"],
                daily_volume=int(row["daily_volume"]),
                avg_units_per_order=int(row["avg_units_per_order"]),
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
        return result
    except Exception as e:
        st.error("Database load failed (order types): " + str(e))
        return None


# ── Combined helpers ──────────────────────────────────────────────────────────

def save_all(areas: List[StorageArea], order_types: List[OrderType]) -> bool:
    ok1 = save_areas(areas)
    ok2 = save_order_types(order_types)
    return ok1 and ok2


def load_all():
    """
    Returns (areas, order_types). Falls back to defaults for whichever
    part is missing or if the database isn't configured at all.
    """
    areas = load_areas()
    order_types = load_order_types()
    if areas is None:
        areas = [a for a in DEFAULT_AREAS]
    if order_types is None:
        order_types = [o for o in DEFAULT_ORDER_TYPES]
    return areas, order_types
