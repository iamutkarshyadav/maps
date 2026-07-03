"""
Centralized constants for the Zepto VRP pipeline.

On import, this module checks for a user-editable JSON config file
at ../data/config/settings.json. If it exists, values from the JSON
override the defaults below. The web UI writes to this JSON file.
"""
import math
import os
import json


# ─── Config File Path ─────────────────────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.normpath(os.path.join(_THIS_DIR, '..', 'data', 'config'))
CONFIG_FILE = os.path.join(CONFIG_DIR, 'settings.json')


# ─── Defaults ─────────────────────────────────────────────────────────
# These are the baseline values. They can be overridden via settings.json.

TRUCK_CAPACITIES = {
    '6FT': 1000,
    '8FT': 2000,
    '10FT': 3000,
    '14FT': 4667,
    '17FT': 5467,
    '20FT': 7600,
    '22FT': 8400,
    '24FT': 8934,
    '32_FT_SXL': 12500,
    '32_FT_MXL': 15000,
}

VEHICLE_RESTRICTION_ALIASES = {
    '6FT_TRUCK': '6FT',
    '8FT_TRUCK': '8FT',
    '10FT_TRUCK': '10FT',
    '14FT_TRUCK': '14FT',
    '17FT_TRUCK': '17FT',
    '20FT_TRUCK': '20FT',
    '22FT_TRUCK': '22FT',
    '24FT_TRUCK': '24FT',
    '32FT_TRUCK': '32_FT_SXL',
    '40FT_TRUCK': '32_FT_MXL',
}

LOADING_TIMES = {
    '6FT': 60,
    '8FT': 60,
    '10FT': 90,
    '14FT': 90,
    '17FT': 120,
    '20FT': 120,
    '22FT': 120,
    '24FT': 120,
    '32_FT_SXL': 120,
    '32_FT_MXL': 120,
}

UNLOADING_TIMES = {
    '6FT': 60,
    '8FT': 60,
    '10FT': 90,
    '14FT': 90,
    '17FT': 120,
    '20FT': 120,
    '22FT': 120,
    '24FT': 120,
    '32_FT_SXL': 120,
    '32_FT_MXL': 120,
}

MH_DOCK_OVERHEAD = 60   # minutes
DH_DOCK_OVERHEAD = 60   # minutes

CONTRACT_CONFIG = {
    '12H': {
        'total_hours': 12,
        'effective_hours': 11,
        'effective_minutes': 660,
        'num_trips': 1,
        'minutes_per_trip': 660,
    },
    '24H': {
        'total_hours': 24,
        'effective_hours': 20,
        'effective_minutes': 1200,
        'num_trips': 2,
        'minutes_per_trip': 600,
    },
}

MIXED_CONTRACT_MODE = True

# Solver time limit (seconds)
SOLVER_TIME_LIMIT = 60

# Maximum DH stops per route (milk run cap)
MAX_STOPS_PER_ROUTE = 3


# ─── Load JSON Overrides ──────────────────────────────────────────────

def _load_config():
    """Load settings.json and override module-level variables."""
    global TRUCK_CAPACITIES, LOADING_TIMES, UNLOADING_TIMES
    global MH_DOCK_OVERHEAD, DH_DOCK_OVERHEAD
    global CONTRACT_CONFIG, MIXED_CONTRACT_MODE, SOLVER_TIME_LIMIT
    global MAX_STOPS_PER_ROUTE

    if not os.path.exists(CONFIG_FILE):
        return

    try:
        with open(CONFIG_FILE, 'r') as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, IOError):
        return

    if 'truck_capacities' in cfg:
        TRUCK_CAPACITIES = cfg['truck_capacities']
    if 'loading_times' in cfg:
        LOADING_TIMES = cfg['loading_times']
    if 'unloading_times' in cfg:
        UNLOADING_TIMES = cfg['unloading_times']
    if 'mh_dock_overhead' in cfg:
        MH_DOCK_OVERHEAD = int(cfg['mh_dock_overhead'])
    if 'dh_dock_overhead' in cfg:
        DH_DOCK_OVERHEAD = int(cfg['dh_dock_overhead'])
    if 'contract_config' in cfg:
        CONTRACT_CONFIG = cfg['contract_config']
    if 'mixed_contract_mode' in cfg:
        MIXED_CONTRACT_MODE = bool(cfg['mixed_contract_mode'])
    if 'solver_time_limit' in cfg:
        SOLVER_TIME_LIMIT = int(cfg['solver_time_limit'])
    if 'max_stops_per_route' in cfg:
        MAX_STOPS_PER_ROUTE = int(cfg['max_stops_per_route'])


_load_config()


def save_config(data):
    """Write the settings dict to settings.json."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def get_current_config():
    """Return a dict of all current settings (for the web UI)."""
    return {
        'truck_capacities': TRUCK_CAPACITIES,
        'loading_times': LOADING_TIMES,
        'unloading_times': UNLOADING_TIMES,
        'mh_dock_overhead': MH_DOCK_OVERHEAD,
        'dh_dock_overhead': DH_DOCK_OVERHEAD,
        'contract_config': CONTRACT_CONFIG,
        'mixed_contract_mode': MIXED_CONTRACT_MODE,
        'solver_time_limit': SOLVER_TIME_LIMIT,
        'max_stops_per_route': MAX_STOPS_PER_ROUTE,
    }


# ─── Helper Functions ─────────────────────────────────────────────────

def resolve_truck_type(restriction_str):
    """
    Resolve an S&OP 'Vehicle restrictions' value to a canonical truck
    type name. Handles both old format ('14FT_TRUCK') and new format ('14FT').
    Returns None if the restriction cannot be resolved.
    """
    clean = str(restriction_str).strip()
    if clean in VEHICLE_RESTRICTION_ALIASES:
        return VEHICLE_RESTRICTION_ALIASES[clean]
    if clean in TRUCK_CAPACITIES:
        return clean
    return None


def get_truck_capacity(restriction_str):
    """
    Get the weight capacity (kg) for a truck type from an S&OP restriction
    string. Falls back to the largest available truck if unrecognized.
    """
    resolved = resolve_truck_type(restriction_str)
    if resolved and resolved in TRUCK_CAPACITIES:
        return TRUCK_CAPACITIES[resolved]
    return max(TRUCK_CAPACITIES.values())


def calculate_vehicles_needed(total_demand, truck_type, buffer=2):
    """
    Dynamically calculate the upper bound of virtual vehicles needed
    for a given truck type to serve the total demand of an MH.

    Args:
        total_demand: Total kg demand across all DHs for this MH.
        truck_type:   Canonical truck type name (e.g., '14FT').
        buffer:       Extra vehicles added as safety margin.

    Returns:
        Integer upper bound of vehicles needed.
    """
    cap = TRUCK_CAPACITIES.get(truck_type, 1)
    if cap <= 0:
        return buffer
    return math.ceil(total_demand / cap) + buffer
