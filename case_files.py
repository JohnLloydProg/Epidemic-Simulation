"""
case_files.py — open, save and delete test cases (sim_data/cases/<name>.json) together with their routing caches.

A case file holds only the changes from sim_data/base/ (see graphing/data_loader.py). Its routing cache lives in
sim_data/cache/routing_<case>_<hash>.pkl; the hash covers the base data and the case file, so editing a case
by hand simply makes the program compute a new cache the next time the case is opened.

    list_cases()                       every case file, newest first, with description and cache status
    save(sim, name, overwrite)         write the open case + this session's changes as <name>.json and write
                                       the routing cache in memory as that case's cache file
    delete(name)                       remove a case file and its cache files
    session_fingerprint(sim)           compare before/after to know if there are unsaved changes

Opening a case is Simulation.open_case(file_name) (it reloads the network); the UI is ui/case_manager.py.
"""
from __future__ import annotations
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from graphing.data_loader import (cases_dir, case_file_name, cache_file, cache_files_of, _base_digest,
                                  read_case, set_case_file)

LOGGER = logging.getLogger('CaseFiles')

NAME_RE = re.compile(r'[A-Za-z0-9_.\-]+')


@dataclass
class CaseInfo:
    file_name: str          # e.g. "00_baseline.json"
    case_id: str
    description: str
    modified: datetime
    has_cache: bool         # a current routing cache exists (opening it will not recompute routes)
    is_open: bool


def clean_name(text:str) -> str:
    """Case name from what was typed: spaces become "_", ".json" is dropped. Raises ValueError if unusable."""
    name = text.strip().replace(' ', '_')
    if name.lower().endswith('.json'):
        name = name[:-5]
    if not name:
        raise ValueError("Type a name for the case.")
    if not NAME_RE.fullmatch(name) or name.startswith('.'):
        raise ValueError("Use only letters, numbers, '_', '-' and '.' in the name.")
    return name


def list_cases() -> list[CaseInfo]:
    folder = cases_dir()
    if not folder.exists():
        return []
    try:
        base = _base_digest()
    except FileNotFoundError:
        base = None
    open_name = case_file_name()
    items = []
    for path in folder.glob('*.json'):
        try:
            case = read_case(path)
        except (OSError, ValueError) as e:
            LOGGER.warning(f"Skipping unreadable case file {path.name}: {e}")
            continue
        has_cache = base is not None and cache_file(path, base, create_dir=False).exists()
        items.append(CaseInfo(path.name, str(case.get('case_id', path.stem)), str(case.get('description') or ''),
                              datetime.fromtimestamp(path.stat().st_mtime), has_cache, path.name == open_name))
    items.sort(key=lambda c: c.modified, reverse=True)
    return items


def case_exists(name:str) -> bool:
    return (cases_dir() / f'{name}.json').exists()


def session_fingerprint(sim) -> str:
    """The case this session would save (name and description left out): differs from the one taken when the
    case was opened or saved exactly when there are unsaved changes."""
    from transport.route_editor import case_from_session
    case, _ = case_from_session(sim)
    case = {k: v for k, v in case.items() if k not in ('case_id', 'description')}
    return json.dumps(case, sort_keys=True, default=str)


def save(sim, name:str, overwrite:bool = False, on_progress=None) -> tuple[str, str]:
    """Save the open case plus this session's changes as <name>.json and make it the open case.
    The routing cache in memory is written as the new case's cache file (rebuilt first if road or tricycle
    changes have not been applied yet). Returns (case file path, note about the routing cache)."""
    from transport.route_editor import save_case
    from routing_table import save_routing_cache

    name = clean_name(name)
    if getattr(sim, 'network_dirty', False) and not sim.started:
        from transport.closures import finish_network_changes   # pending road/tricycle changes: apply them
        finish_network_changes(sim, on_progress)
    stale_cache = getattr(sim, 'network_dirty', False)            # only possible after the start

    old_caches = cache_files_of(name)
    path = Path(save_case(sim, name, overwrite=overwrite))
    set_case_file(path.name)               # the saved case is now the open one (workers load it too)

    if stale_cache:
        note = "routing cache not saved (road changes not applied yet) — it is computed when the case is opened"
    else:
        target = cache_file(path)
        try:
            pairs = save_routing_cache(target, sim.routing_table, sim.routes)
            note = f"routing cache saved ({pairs:,} trips)"
        except Exception as e:             # the case file is saved either way
            LOGGER.exception("Could not save the routing cache")
            target = None
            note = f"routing cache NOT saved ({e}) — it is computed when the case is opened"
        for old in old_caches:             # caches of the file this one replaced
            if target is None or old != target:
                old.unlink(missing_ok=True)
    sim.case_fingerprint = session_fingerprint(sim)
    LOGGER.info(f"Saved case {path} ({note}).")
    return str(path), note


def delete(file_name:str) -> int:
    """Delete a case file and its routing caches. Returns the number of cache files removed."""
    if file_name == case_file_name():
        raise ValueError("That case is open. Open another case first.")
    path = cases_dir() / file_name
    case_id = read_case(path)['case_id'] if path.exists() else Path(file_name).stem
    path.unlink(missing_ok=True)
    caches = cache_files_of(case_id)
    # another case file could share the case_id (e.g. a copy made by hand): keep caches it still uses
    in_use = {c.case_id for c in list_cases()}
    removed = 0
    if case_id not in in_use:
        for cache in caches:
            cache.unlink(missing_ok=True)
            removed += 1
    LOGGER.info(f"Deleted case {file_name} and {removed} routing cache file(s).")
    return removed
