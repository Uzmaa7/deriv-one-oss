"""Infrastructure monitoring API — pod CPU / memory metrics for the admin dashboard.

Queries the in-cluster Kubernetes Metrics API (metrics.k8s.io/v1beta1) and the
core Pods API to return live resource utilisation for every pod in the
the configured sandbox namespace.

Endpoint
────────
GET /api/admin/infra   (admin only)

Returns
───────
{
  "pods": [
    {
      "name": "backend-6475f5db7",
      "workload": "backend",
      "phase": "Running",
      "ready": true,
      "node": "node-1",
      "age_seconds": 3600,
      "containers": [
        {
          "name": "backend",
          "cpu_m": 42,          // millicores used
          "mem_mi": 128,        // MiB used
          "cpu_limit_m": null,  // null when no limit set
          "mem_limit_mi": 1024
        }
      ],
      "cpu_m_total": 42,
      "mem_mi_total": 128
    }
  ],
  "fetched_at": "2026-06-11T06:30:00Z"
}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from collections import deque

from fastapi import APIRouter, Request

from cf_access import build_auth_context

logger = logging.getLogger(__name__)
router = APIRouter()

_NAMESPACE = os.environ.get("SANDBOX_NAMESPACE", "agent-sandbox")
_INFRA_ENABLED = os.environ.get("INFRA_MONITORING_ENABLED", "auto").strip().lower()
_INFRA_WORKLOAD_ALLOWLIST = frozenset(
    w.strip()
    for w in os.environ.get(
        "INFRA_WORKLOAD_ALLOWLIST",
        "backend,frontend,slack-consumer,scheduler,sandbox-router,sandbox-executor",
    ).split(",")
    if w.strip()
)


def _infra_monitoring_enabled() -> bool:
    if _INFRA_ENABLED in ("0", "false", "no", "off"):
        return False
    if _INFRA_ENABLED in ("1", "true", "yes", "on"):
        return True
    # auto: only attempt k8s metrics when the standard in-cluster env is present
    return bool(os.environ.get("KUBERNETES_SERVICE_HOST", "").strip())

# ── In-memory metrics history (ring buffer) ───────────────────────────────────
# Each entry: {
#   "ts": ISO str,
#   "workloads": {workload: {"cpu_m": int, "mem_mi": int, "pods": int, "ready": int}},
#   "pools": {pool_name: {"configured": int, "total": int, "ready": int, "in_use": int}}
# }
# Persisted to Redis so history survives pod restarts.
# Keeps ~30 min at 15-second poll cadence = 120 entries max.
_HISTORY_MAX = 120
_REDIS_KEY   = "infra:history"
_history: deque[dict] = deque(maxlen=_HISTORY_MAX)
_history_loaded = False

_REDIS_HOST = os.environ.get("REDIS_HOST", "").strip()
_REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
_REDIS_DB = int(os.environ.get("REDIS_DB", "0"))


def _redis_client():
    import redis
    return redis.Redis(host=_REDIS_HOST, port=_REDIS_PORT, db=_REDIS_DB, decode_responses=True)


def _load_history_from_redis() -> None:
    """Populate _history from Redis on first call. Safe to call repeatedly."""
    global _history_loaded
    if _history_loaded or not _REDIS_HOST:
        _history_loaded = True
        return
    try:
        r = _redis_client()
        raw_items = r.lrange(_REDIS_KEY, 0, _HISTORY_MAX - 1)
        # Redis list is newest-first (LPUSH); reverse to get chronological order
        for item in reversed(raw_items):
            try:
                _history.append(json.loads(item))
            except Exception:
                pass
        logger.info("api_infra: loaded %d history points from Redis", len(_history))
    except Exception as exc:
        logger.warning("api_infra: could not load history from Redis: %s", exc)
    finally:
        _history_loaded = True


def _persist_snapshot(snap: dict) -> None:
    """Push the latest snapshot to Redis and trim to _HISTORY_MAX entries."""
    if not _REDIS_HOST:
        return
    try:
        r = _redis_client()
        pipe = r.pipeline()
        pipe.lpush(_REDIS_KEY, json.dumps(snap))
        pipe.ltrim(_REDIS_KEY, 0, _HISTORY_MAX - 1)
        pipe.execute()
    except Exception as exc:
        logger.warning("api_infra: Redis persist failed: %s", exc)

# ── Kubernetes client (lazy, in-cluster) ──────────────────────────────────────

_k8s_ready = False


def _ensure_k8s() -> None:
    global _k8s_ready
    if _k8s_ready:
        return
    try:
        # Optional: present only when running under an orchestrator.
        from kubernetes_asyncio import config as k8s_config  # type: ignore[import]
        k8s_config.load_incluster_config()
        _k8s_ready = True
        logger.info("api_infra: in-cluster k8s config loaded")
    except Exception:
        logger.warning("api_infra: not running in-cluster — k8s metrics unavailable")


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _parse_cpu_m(cpu_str: Optional[str]) -> Optional[int]:
    """Convert a Kubernetes CPU quantity string to millicores (int)."""
    if not cpu_str:
        return None
    s = cpu_str.strip()
    if s.endswith("n"):
        return max(1, int(s[:-1]) // 1_000_000)
    if s.endswith("u"):
        return max(1, int(s[:-1]) // 1_000)
    if s.endswith("m"):
        return int(s[:-1])
    try:
        return int(float(s) * 1000)
    except ValueError:
        return None


def _parse_mem_mi(mem_str: Optional[str]) -> Optional[int]:
    """Convert a Kubernetes memory quantity string to MiB (int)."""
    if not mem_str:
        return None
    s = mem_str.strip()
    units = {
        "Ki": 1 / 1024,
        "Mi": 1,
        "Gi": 1024,
        "Ti": 1024 * 1024,
        "k":  1 / 1024,
        "M":  1000 / 1024,
        "G":  1_000_000 / 1024,
    }
    for suffix, factor in units.items():
        if s.endswith(suffix):
            try:
                return max(0, int(float(s[: -len(suffix)]) * factor))
            except ValueError:
                return None
    try:
        return max(0, int(s) // (1024 * 1024))
    except ValueError:
        return None


def _workload_name(pod_name: str, labels: dict) -> str:
    """Derive a human-readable workload name from labels or pod name."""
    for lbl in ("app", "app.kubernetes.io/name"):
        if lbl in labels:
            return labels[lbl]
    # Fall back: strip trailing hash suffix (e.g. backend-6475f5db7-8k86p → backend)
    parts = pod_name.rsplit("-", 2)
    return parts[0] if len(parts) >= 3 else pod_name


# ── Shared metrics collector (used by background loop AND HTTP handler) ────────

async def _collect_metrics() -> dict[str, Any]:
    """Collect pod metrics + warm pool status, record a history snapshot, and
    return the raw data needed by the HTTP endpoint.  Never raises — returns
    an error dict on failure."""
    if not _infra_monitoring_enabled():
        return {
            "pods": [],
            "pools": [],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "enabled": False,
            "error": "Infrastructure monitoring is disabled outside Kubernetes",
        }
    _ensure_k8s()
    if not _k8s_ready:
        return {
            "pods": [],
            "pools": [],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "enabled": False,
            "error": "Metrics API unavailable (not in-cluster)",
        }

    from kubernetes_asyncio import client as k8s_client  # type: ignore[import]
    from kubernetes_asyncio.client import ApiClient  # type: ignore[import]

    pods_out: list[dict] = []

    async with ApiClient() as api:
        core   = k8s_client.CoreV1Api(api)
        custom = k8s_client.CustomObjectsApi(api)

        try:
            pod_list = await core.list_namespaced_pod(namespace=_NAMESPACE)
        except Exception as exc:
            logger.error("api_infra: failed to list pods: %s", exc)
            return {"pods": [], "pools": [], "fetched_at": datetime.now(timezone.utc).isoformat(), "error": str(exc)}

        limits_map: dict[str, dict[str, dict]] = {}
        for pod in pod_list.items:
            container_limits: dict[str, dict] = {}
            for c in (pod.spec.containers or []):
                res = c.resources
                lim = res.limits if res else None
                req = res.requests if res else None
                container_limits[c.name] = {
                    "cpu_limit_m":  _parse_cpu_m((lim or {}).get("cpu")) if lim else None,
                    "mem_limit_mi": _parse_mem_mi((lim or {}).get("memory")) if lim else None,
                    "cpu_req_m":    _parse_cpu_m((req or {}).get("cpu")) if req else None,
                    "mem_req_mi":   _parse_mem_mi((req or {}).get("memory")) if req else None,
                }
            limits_map[pod.metadata.name] = container_limits

        metrics_map: dict[str, dict[str, dict]] = {}
        try:
            raw = await custom.list_namespaced_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                namespace=_NAMESPACE,
                plural="pods",
            )
            for item in raw.get("items", []):
                pname = item["metadata"]["name"]
                metrics_map[pname] = {
                    c["name"]: {
                        "cpu_m":  _parse_cpu_m(c["usage"].get("cpu")),
                        "mem_mi": _parse_mem_mi(c["usage"].get("memory")),
                    }
                    for c in item.get("containers", [])
                }
        except Exception as exc:
            logger.warning("api_infra: metrics API unavailable: %s", exc)

        now = datetime.now(timezone.utc)
        for pod in pod_list.items:
            meta   = pod.metadata
            spec   = pod.spec
            status = pod.status

            labels   = dict(meta.labels or {})
            workload = _workload_name(meta.name, labels)

            ready = False
            if status.conditions:
                for cond in status.conditions:
                    if cond.type == "Ready":
                        ready = cond.status == "True"
                        break

            age_s = 0
            if meta.creation_timestamp:
                ts = meta.creation_timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_s = max(0, int((now - ts).total_seconds()))

            pod_metrics = metrics_map.get(meta.name, {})
            pod_limits  = limits_map.get(meta.name, {})

            containers_out = []
            cpu_total = 0
            mem_total = 0
            for c in (spec.containers or []):
                cm = pod_metrics.get(c.name, {})
                cl = pod_limits.get(c.name, {})
                cpu_m  = cm.get("cpu_m")
                mem_mi = cm.get("mem_mi")
                if cpu_m:
                    cpu_total += cpu_m
                if mem_mi:
                    mem_total += mem_mi
                containers_out.append({
                    "name":         c.name,
                    "cpu_m":        cpu_m,
                    "mem_mi":       mem_mi,
                    "cpu_limit_m":  cl.get("cpu_limit_m"),
                    "mem_limit_mi": cl.get("mem_limit_mi"),
                    "cpu_req_m":    cl.get("cpu_req_m"),
                    "mem_req_mi":   cl.get("mem_req_mi"),
                })

            pods_out.append({
                "name":         meta.name,
                "workload":     workload,
                "phase":        status.phase or "Unknown",
                "ready":        ready,
                "node":         spec.node_name or "",
                "age_seconds":  age_s,
                "containers":   containers_out,
                "cpu_m_total":  cpu_total,
                "mem_mi_total": mem_total,
            })

    if _INFRA_WORKLOAD_ALLOWLIST:
        pods_out = [p for p in pods_out if p["workload"] in _INFRA_WORKLOAD_ALLOWLIST]

    pods_out.sort(key=lambda p: (p["workload"], p["name"]))

    pools_out: list[dict] = []
    try:
        async with ApiClient() as api2:
            custom2    = k8s_client.CustomObjectsApi(api2)
            warm_pools = await custom2.list_namespaced_custom_object(
                group="extensions.agents.x-k8s.io",
                version="v1alpha1",
                namespace=_NAMESPACE,
                plural="sandboxwarmpools",
            )
        for item in warm_pools.get("items", []):
            pool_name  = item["metadata"]["name"]
            configured = item["spec"].get("replicas", 0)
            pool_st    = item.get("status", {})
            total      = pool_st.get("replicas", 0)
            ready_c    = pool_st.get("readyReplicas", 0)
            in_use     = max(0, total - ready_c)
            pools_out.append({
                "name":       pool_name,
                "configured": configured,
                "total":      total,
                "ready":      ready_c,
                "in_use":     in_use,
            })
    except Exception as exc:
        logger.warning("api_infra: could not fetch warm pool status: %s", exc)

    # Record history snapshot
    workload_agg: dict[str, dict] = {}
    for p in pods_out:
        w = p["workload"]
        if w not in workload_agg:
            workload_agg[w] = {"cpu_m": 0, "mem_mi": 0, "pods": 0, "ready": 0}
        workload_agg[w]["cpu_m"]  += p["cpu_m_total"]
        workload_agg[w]["mem_mi"] += p["mem_mi_total"]
        workload_agg[w]["pods"]   += 1
        workload_agg[w]["ready"]  += 1 if p["ready"] else 0

    pool_snap: dict[str, dict] = {
        p["name"]: {
            "configured": p["configured"],
            "total":      p["total"],
            "ready":      p["ready"],
            "in_use":     p["in_use"],
        }
        for p in pools_out
    }
    snap = {"ts": now.isoformat(), "workloads": workload_agg, "pools": pool_snap}
    _history.append(snap)
    _persist_snapshot(snap)

    return {"pods": pods_out, "pools": pools_out, "fetched_at": now.isoformat()}


# ── Background snapshot collector ─────────────────────────────────────────────
_SNAPSHOT_INTERVAL = 30  # seconds between background collections

async def _snapshot_loop() -> None:
    """Runs forever: collect metrics every _SNAPSHOT_INTERVAL seconds.
    Started at app startup so history accumulates even when no one is
    viewing the Infrastructure page."""
    _load_history_from_redis()
    logger.info("api_infra: background snapshot loop started (interval=%ds)", _SNAPSHOT_INTERVAL)
    while True:
        try:
            await _collect_metrics()
        except Exception as exc:
            logger.warning("api_infra: snapshot loop error: %s", exc)
        await asyncio.sleep(_SNAPSHOT_INTERVAL)


def start_background_collector() -> None:
    """Schedule the background snapshot loop on the running event loop.
    Call this once from the FastAPI startup handler."""
    asyncio.get_event_loop().create_task(_snapshot_loop())


# ── Main API handler ───────────────────────────────────────────────────────────

@router.get("/api/admin/infra")
async def get_infra(request: Request) -> dict[str, Any]:
    """Return live CPU / memory metrics for all pods in the namespace."""
    ctx = build_auth_context(request.headers)
    if not ctx.is_admin:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin access required.")

    return await _collect_metrics()


@router.get("/api/admin/infra/history")
async def get_infra_history(request: Request) -> dict[str, Any]:
    """Return the rolling 30-minute per-workload CPU/memory history."""
    ctx = build_auth_context(request.headers)
    if not ctx.is_admin:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin access required.")
    _load_history_from_redis()

    # Collect all workload and pool names seen across all snapshots
    all_workloads: set[str] = set()
    all_pools: set[str] = set()
    for snap in _history:
        all_workloads.update(snap["workloads"].keys())
        all_pools.update(snap.get("pools", {}).keys())

    # Build per-workload time series
    series: dict[str, list[dict]] = {w: [] for w in all_workloads}
    for snap in _history:
        ts = snap["ts"]
        for w in all_workloads:
            agg = snap["workloads"].get(w)
            series[w].append({
                "ts":     ts,
                "cpu_m":  agg.get("cpu_m", 0)  if agg else 0,
                "mem_mi": agg.get("mem_mi", 0) if agg else 0,
                "pods":   agg.get("pods", 0)   if agg else 0,
                "ready":  agg.get("ready", 0)  if agg else 0,
            })

    # Build per-pool utilization time series
    pool_series: dict[str, list[dict]] = {p: [] for p in all_pools}
    for snap in _history:
        ts = snap["ts"]
        for p in all_pools:
            ps = snap.get("pools", {}).get(p)
            pool_series[p].append({
                "ts":         ts,
                "configured": ps["configured"] if ps else 0,
                "total":      ps["total"]      if ps else 0,
                "ready":      ps["ready"]      if ps else 0,
                "in_use":     ps["in_use"]     if ps else 0,
            })

    return {
        "series":       series,
        "workloads":    sorted(all_workloads),
        "pool_series":  pool_series,
        "pools":        sorted(all_pools),
        "points":       len(_history),
    }
