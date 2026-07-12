# -*- coding: utf-8 -*-
"""RSSHub namespace discovery proxy — fetch, cache (6h), and flatten the route tree.

The self-hosted RSSHub instance exposes ``GET /api/namespace`` returning the full
route metadata (~1590 namespaces / ~3289 routes, ~3.3MB). We proxy it with a
server-side cache so RSSHub is hit at most once per TTL, and serve a flattened,
search-ready route list to the frontend.

Robustness: the RSSHub instance may be down intermittently. On fetch failure we
serve the stale cached blob if present; otherwise we surface an error so the
frontend can show a degraded state instead of crashing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from src.config import Config
from api.v1.endpoints._rss_cache import _cache_get, _cache_put
from api.v1.endpoints._rss_filter import is_hidden_from_explore

logger = logging.getLogger(__name__)

BLOB_CACHE_KEY = "rss:namespaces:blob:v1"
DETAIL_CACHE_KEY_FMT = "rss:namespace:{ns}:v1"
BLOB_TTL_SECONDS = 6 * 3600  # route metadata rarely changes
FETCH_TIMEOUT = 20.0

# Pure crypto namespaces — excluded from the stock-focused view. Everything else
# in the `finance` category is kept (forex, commodities, macro, central bank all
# move markets). New upstream crypto namespaces won't auto-match; revisit if needed.
CRYPTO_NAMESPACES = {
    "binance", "bitget", "okx", "coindesk", "cointelegraph", "cryptoslate",
    "decrypt", "forklog", "hyperdash", "jinse", "paradigm", "polymarket",
    "techflowpost", "theblock", "theblockbeats", "tokeninsight",
}

# Fields kept per route when flattening (drop the heavy markdown description by
# default — frontend can fetch detail lazily if ever needed).
_ROUTE_FIELDS = (
    "path", "name", "url", "example", "categories",
    "description", "parameters", "features", "maintainers",
)


def _rsshub_base() -> str:
    return Config.get_instance().rsshub_base_url.rstrip("/")


def _is_stale_ok(blob: Optional[dict], ttl: int = BLOB_TTL_SECONDS) -> bool:
    """True if a cached blob exists and is within TTL (or fresh enough to serve stale)."""
    if not isinstance(blob, dict):
        return False
    fetched = blob.get("_fetched_at")
    if not fetched:
        # No timestamp: serve anyway (better than nothing) but mark stale.
        return True
    try:
        ts = datetime.fromisoformat(fetched)
    except Exception:
        return True
    return (datetime.now() - ts) < timedelta(seconds=ttl)


def _fetch_json(url: str) -> Optional[dict]:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        }
        resp = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("[RSS] namespace fetch failed %s: %s", url, exc)
        return None


def _get_namespaces_raw(force: bool = False) -> Dict[str, Any]:
    """Return the raw namespace tree (dict keyed by namespace id), with cache metadata.

    Response shape: ``{"data": {<ns_id>: {...}}, "_fetched_at", "_cached", "_stale", "_error"}``.
    On fetch failure, serves the stale cached blob if available.
    """
    cached = _cache_get(BLOB_CACHE_KEY)
    if not force and _is_stale_ok(cached):
        cached["_cached"] = True
        cached["_stale"] = False
        cached["_error"] = None
        return cached

    url = f"{_rsshub_base()}/api/namespace"
    raw = _fetch_json(url)
    if raw is None:
        # RSSHub unavailable — fall back to stale blob if we have one.
        if isinstance(cached, dict) and cached.get("data"):
            logger.info("[RSS] RSSHub unavailable; serving stale namespace blob.")
            cached["_cached"] = True
            cached["_stale"] = True
            cached["_error"] = "RSSHub 实例暂不可用，返回上次缓存的路由数据"
            return cached
        return {
            "data": {},
            "_fetched_at": None,
            "_cached": False,
            "_stale": False,
            "_error": "RSSHub 实例不可用且无缓存数据",
        }

    # RSSHub returns the namespace map directly (not nested under "data").
    data = raw if isinstance(raw, dict) else {}
    blob = {
        "data": data,
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
        "_stale": False,
        "_error": None,
    }
    try:
        _cache_put(BLOB_CACHE_KEY, blob)
    except Exception as exc:
        logger.warning("[RSS] namespace blob cache write failed: %s", exc)
    return blob


def _flatten_routes(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten the namespace tree into a list of route descriptors.

    Each entry: ``{namespace, namespace_name, route_path, name, url, example,
    categories, description, parameters, features, maintainers}``.
    ``route_path`` is prefixed with the namespace so it is directly buildable
    (e.g. ``/wallstreetcn/news/:category?``).
    """
    routes: List[Dict[str, Any]] = []
    if not isinstance(raw, dict):
        return routes
    # RSSHub exposes the same route under multiple keys when it has path
    # aliases (e.g. futunn "/main" and "/" both map to path ["/main", "/"]).
    # Dedupe by (namespace, route_path) so the explore list shows each route
    # once — keeping the first occurrence (the canonical key, e.g. "/main").
    seen: set[tuple[str, str]] = set()
    for ns_id, ns_info in raw.items():
        if not isinstance(ns_info, dict):
            continue
        ns_name = ns_info.get("name") or ns_id
        ns_routes = ns_info.get("routes") or {}
        if not isinstance(ns_routes, dict):
            continue
        for _route_key, route in ns_routes.items():
            if not isinstance(route, dict):
                continue
            raw_path = route.get("path")
            if not raw_path:
                continue
            # RSSHub `path` can be a string or string[] (path aliases).
            if isinstance(raw_path, list):
                paths = [p for p in raw_path if isinstance(p, str) and p]
                if not paths:
                    continue
                path = paths[0]
            elif isinstance(raw_path, str):
                path = raw_path
            else:
                continue
            # Ensure route_path starts with "/{namespace}/".
            ns_prefix = f"/{ns_id}"
            if path.startswith(ns_prefix):
                route_path = path
            elif path.startswith("/"):
                route_path = f"{ns_prefix}{path}"
            else:
                route_path = f"{ns_prefix}/{path}"
            dedupe_key = (ns_id, route_path)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            entry: Dict[str, Any] = {
                "namespace": ns_id,
                "namespace_name": ns_name,
                "route_path": route_path,
                "name": route.get("name") or "",
                "url": route.get("url") or "",
                "example": route.get("example") or "",
                "categories": route.get("categories") or [],
                "description": route.get("description") or "",
                "parameters": route.get("parameters") or {},
                "features": route.get("features") or {},
                "maintainers": route.get("maintainers") or [],
            }
            routes.append(entry)
    return routes


def get_namespaces_flat(force: bool = False, finance_only: bool = False) -> Dict[str, Any]:
    """Flattened route list + metadata for the frontend discovery browser.

    When ``finance_only`` is set, only routes in the ``finance`` category whose
    namespace is not a pure-crypto namespace are returned — i.e. everything that
    moves stock markets (A-share, HK/US, forex, commodities, macro, central bank).
    """
    blob = _get_namespaces_raw(force=force)
    data = blob.get("data") or {}
    routes = _flatten_routes(data)
    if finance_only:
        routes = [
            r for r in routes
            if "finance" in (r.get("categories") or [])
            and r.get("namespace") not in CRYPTO_NAMESPACES
        ]
    # Apply the explore-visibility filter (broken / English-only / unuseful)
    # so the backend serves the curated catalog directly — single source of
    # truth shared by the RSS explore page and the AI assistant catalog.
    routes = [r for r in routes if not is_hidden_from_explore(r)]
    # Derive categories from the (possibly filtered) set (deduped, sorted).
    cat_set: set[str] = set()
    for r in routes:
        for c in r.get("categories") or []:
            if isinstance(c, str) and c:
                cat_set.add(c)
    return {
        "routes": routes,
        "namespace_count": len({r.get("namespace") for r in routes if r.get("namespace")}),
        "route_count": len(routes),
        "categories": sorted(cat_set),
        "_fetched_at": blob.get("_fetched_at"),
        "_cached": bool(blob.get("_cached")),
        "_stale": bool(blob.get("_stale")),
        "_error": blob.get("_error"),
    }


def get_namespace_detail(ns: str, force: bool = False) -> Dict[str, Any]:
    """Single namespace detail (proxies /api/namespace/{ns}), 6h cached."""
    key = DETAIL_CACHE_KEY_FMT.format(ns=ns)
    cached = _cache_get(key)
    if not force and _is_stale_ok(cached):
        cached["_cached"] = True
        cached["_stale"] = False
        cached["_error"] = None
        return cached

    url = f"{_rsshub_base()}/api/namespace/{ns}"
    raw = _fetch_json(url)
    if raw is None:
        if isinstance(cached, dict) and cached.get("data"):
            cached["_cached"] = True
            cached["_stale"] = True
            cached["_error"] = "RSSHub 实例暂不可用，返回上次缓存"
            return cached
        return {
            "namespace": ns,
            "name": ns,
            "routes": {},
            "_fetched_at": None,
            "_cached": False,
            "_stale": False,
            "_error": "RSSHub 实例不可用且无缓存",
        }

    blob = {
        "namespace": ns,
        "name": (raw.get("name") if isinstance(raw, dict) else None) or ns,
        "routes": (raw.get("routes") if isinstance(raw, dict) else {}) or {},
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
        "_stale": False,
        "_error": None,
    }
    try:
        _cache_put(key, blob)
    except Exception as exc:
        logger.warning("[RSS] namespace detail cache write failed: %s", exc)
    return blob


def get_categories(force: bool = False, finance_only: bool = False) -> List[str]:
    """All route categories (derived from the cached blob)."""
    return get_namespaces_flat(force=force, finance_only=finance_only).get("categories") or []
