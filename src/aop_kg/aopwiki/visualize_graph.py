"""Create an interactive WebGL visualization for converted AOP-Wiki graphs.

This intentionally does not use NetworkX. Graph operations and layout are done
with python-igraph's C core, and the browser rendering is a self-contained
Canvas implementation so the generated HTML works offline.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import igraph as ig

DEFAULT_COLORS = {
    "aop": "#7b3294",
    "key-event": "#008837",
    "key-event-relationship": "#fdae61",
    "stressor": "#d7191c",
    "chemical": "#2c7bb6",
    "taxonomy": "#1a9641",
    "biological-object": "#80cdc1",
    "biological-process": "#35978f",
    "biological-action": "#01665e",
}
FALLBACK_COLOR = "#8c8c8c"

NODE_FIELDS = ["id", "label", "type", "name", "source", "source_id", "uri", "modifiers"]
EDGE_FIELDS = ["source", "target", "type", "id", "label", "properties_json"]


def raise_csv_field_limit() -> None:
    """Allow large narrative/evidence JSON fields in edges.csv."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


def read_nodes(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(NODE_FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} is missing expected columns: {sorted(missing)}")
        return {row["id"]: row for row in reader if row.get("id")}


def read_edges(path: Path, node_ids: set[str]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(EDGE_FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} is missing expected columns: {sorted(missing)}")
        for row in reader:
            if row.get("source") in node_ids and row.get("target") in node_ids:
                edges.append(row)
    return edges


def hide_relationship_nodes(
    nodes: dict[str, dict[str, str]],
    edges: list[dict[str, str]],
) -> tuple[dict[str, dict[str, str]], list[dict[str, str]]]:
    """Remove key-event-relationship records as nodes.

    The converted CSV keeps KER records both as schema entities and as direct
    KEY_EVENT_RELATIONSHIP edges between upstream/downstream key events. For
    visualization, the direct edge is the useful graph representation, so hide
    the entity nodes and their bookkeeping edges by default.
    """
    kept_nodes = {node_id: row for node_id, row in nodes.items() if row.get("type") != "key-event-relationship"}
    kept_node_ids = set(kept_nodes)
    kept_edges = [edge for edge in edges if edge.get("source") in kept_node_ids and edge.get("target") in kept_node_ids]
    return kept_nodes, kept_edges


def build_graph(nodes: dict[str, dict[str, str]], edges: list[dict[str, str]]) -> tuple[ig.Graph, dict[str, int]]:
    ids = list(nodes)
    id_to_index = {node_id: index for index, node_id in enumerate(ids)}
    graph = ig.Graph(directed=True)
    graph.add_vertices(len(ids))
    graph.vs["id"] = ids
    graph.add_edges((id_to_index[e["source"]], id_to_index[e["target"]]) for e in edges)
    graph.es["type"] = [e.get("type", "") for e in edges]
    return graph, id_to_index


def induced_data(
    graph: ig.Graph,
    nodes: dict[str, dict[str, str]],
    edges: list[dict[str, str]],
    vertex_indices: set[int],
) -> tuple[dict[str, dict[str, str]], list[dict[str, str]]]:
    keep_ids = {graph.vs[index]["id"] for index in vertex_indices}
    kept_nodes = {node_id: nodes[node_id] for node_id in keep_ids}
    kept_edges = [e for e in edges if e["source"] in keep_ids and e["target"] in keep_ids]
    return kept_nodes, kept_edges


def largest_weak_component(graph: ig.Graph) -> set[int]:
    components = graph.components(mode="weak")
    if not components:
        return set()
    return set(max(components, key=len))


def focus_neighborhood(graph: ig.Graph, nodes: dict[str, dict[str, str]], id_to_index: dict[str, int], focus: str, depth: int) -> set[int]:
    if focus in id_to_index:
        starts = [id_to_index[focus]]
    else:
        query = focus.casefold()
        starts = []
        for index, vertex in enumerate(graph.vs):
            row = nodes[vertex["id"]]
            searchable = " ".join([row.get("id", ""), row.get("name", ""), row.get("type", ""), row.get("source_id", ""), row.get("modifiers", "")]).casefold()
            if query in searchable:
                starts.append(index)
    if not starts:
        raise ValueError(f"No node id/name/type/source_id matched --focus {focus!r}")
    result: set[int] = set()
    for neighborhood in graph.neighborhood(starts, order=depth, mode="all", mindist=0):
        result.update(neighborhood)
    return result


def top_degree_vertices(graph: ig.Graph, max_nodes: int) -> set[int]:
    if graph.vcount() <= max_nodes:
        return set(range(graph.vcount()))
    degree = graph.degree(mode="all", loops=False)
    ranked = sorted(range(graph.vcount()), key=lambda i: degree[i], reverse=True)
    return set(ranked[:max_nodes])


def filter_graph(
    graph: ig.Graph,
    nodes: dict[str, dict[str, str]],
    edges: list[dict[str, str]],
    id_to_index: dict[str, int],
    *,
    node_types: set[str] | None,
    largest_component_only: bool,
    focus: str | None,
    focus_depth: int,
    max_nodes: int,
) -> tuple[dict[str, dict[str, str]], list[dict[str, str]]]:
    keep = set(range(graph.vcount()))

    if node_types:
        keep &= {index for index, vertex in enumerate(graph.vs) if nodes[vertex["id"]].get("type") in node_types}

    if largest_component_only:
        keep &= largest_weak_component(graph)

    if focus:
        keep &= focus_neighborhood(graph, nodes, id_to_index, focus, focus_depth)

    kept_nodes, kept_edges = induced_data(graph, nodes, edges, keep)
    filtered_graph, filtered_id_to_index = build_graph(kept_nodes, kept_edges)

    if max_nodes > 0 and filtered_graph.vcount() > max_nodes:
        keep_top = top_degree_vertices(filtered_graph, max_nodes)
        kept_nodes, kept_edges = induced_data(filtered_graph, kept_nodes, kept_edges, keep_top)

    return kept_nodes, kept_edges


def parse_properties(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return value if isinstance(value, dict) else {"value": value}


def compute_layout(graph: ig.Graph, layout_name: str) -> list[tuple[float, float]]:
    if graph.vcount() == 0:
        return []
    layout_graph = graph.as_undirected(combine_edges=None)
    layout_graph.simplify(multiple=True, loops=True)
    chosen = layout_name
    if chosen == "auto":
        chosen = "drl" if layout_graph.vcount() >= 500 else "fr"
    layout = layout_graph.layout(chosen)
    return [(float(x), float(y)) for x, y in layout.coords]


def finite_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def graph_payload(nodes: dict[str, dict[str, str]], edges: list[dict[str, str]], layout_name: str) -> dict[str, Any]:
    graph, id_to_index = build_graph(nodes, edges)
    coords = compute_layout(graph, layout_name)
    degree = graph.degree(mode="all", loops=False) if graph.vcount() else []
    betweenness = graph.betweenness(directed=False) if graph.vcount() else []
    closeness = graph.closeness(mode="all") if graph.vcount() else []
    pagerank = graph.pagerank(directed=True) if graph.vcount() else []
    edge_betweenness = graph.edge_betweenness(directed=False) if graph.ecount() else []

    type_counts = Counter(row.get("type", "unknown") for row in nodes.values())
    edge_type_counts = Counter(row.get("type", "unknown") for row in edges)

    out_nodes: list[dict[str, Any]] = []
    for node_id, row in nodes.items():
        index = id_to_index[node_id]
        x, y = coords[index]
        node_type = row.get("type", "")
        size = 3.0 + 2.0 * math.log1p(degree[index])
        out_nodes.append(
            {
                "key": node_id,
                "attributes": {
                    "label": row.get("name") or row.get("id") or node_id,
                    "name": row.get("name") or "",
                    "type": node_type,
                    "kind": row.get("label") or "",
                    "source": row.get("source") or "",
                    "source_id": row.get("source_id") or "",
                    "uri": row.get("uri") or "",
                    "modifiers": parse_properties(row.get("modifiers")) if row.get("modifiers", "").startswith("{") else row.get("modifiers") or "",
                    "degree": degree[index],
                    "betweenness": finite_number(betweenness[index]),
                    "closeness": finite_number(closeness[index]),
                    "pagerank": finite_number(pagerank[index]),
                    "x": x,
                    "y": y,
                    "size": size,
                    "color": DEFAULT_COLORS.get(node_type, FALLBACK_COLOR),
                },
            }
        )

    out_edges: list[dict[str, Any]] = []
    for idx, row in enumerate(edges):
        out_edges.append(
            {
                "key": row.get("id") or f"edge-{idx}",
                "source": row["source"],
                "target": row["target"],
                "attributes": {
                    "label": row.get("label") or row.get("type") or "edge",
                    "type": row.get("type") or "",
                    "properties": parse_properties(row.get("properties_json")),
                    "edge_betweenness": finite_number(edge_betweenness[idx]) if idx < len(edge_betweenness) else 0,
                    "size": 1,
                    "color": "rgba(120,120,120,0.35)",
                },
            }
        )

    return {
        "nodes": out_nodes,
        "edges": out_edges,
        "meta": {
            "node_count": len(out_nodes),
            "edge_count": len(out_edges),
            "node_type_counts": dict(sorted(type_counts.items())),
            "edge_type_counts": dict(sorted(edge_type_counts.items())),
        },
    }


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>__TITLE__</title>
  <style>
    html, body { margin: 0; height: 100%; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    #app { display: grid; grid-template-columns: 320px 1fr; height: 100vh; }
    #panel { box-sizing: border-box; padding: 14px; border-right: 1px solid #ddd; overflow: auto; background: #fafafa; }
    #graphWrap { position: relative; height: 100vh; background: #fff; overflow: hidden; }
    #graph { width: 100%; height: 100%; display: block; }
    #hud { position: absolute; left: 10px; bottom: 10px; background: rgba(255,255,255,0.88); border: 1px solid #ddd; border-radius: 4px; padding: 5px 7px; font-size: 12px; color: #555; }
    #tooltip { position: absolute; pointer-events: none; display: none; max-width: 320px; background: rgba(25,25,25,0.88); color: #fff; border-radius: 4px; padding: 6px 8px; font-size: 12px; line-height: 1.35; box-shadow: 0 2px 8px rgba(0,0,0,0.25); z-index: 3; }
    h1 { font-size: 18px; margin: 0 0 8px; }
    h2 { font-size: 14px; margin: 18px 0 8px; }
    .muted { color: #666; font-size: 12px; line-height: 1.4; }
    .stat { display: flex; justify-content: space-between; font-size: 13px; padding: 2px 0; }
    .type-row { display: flex; align-items: center; gap: 7px; font-size: 13px; margin: 4px 0; }
    .swatch { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
    input[type="search"] { width: 100%; box-sizing: border-box; padding: 7px; border: 1px solid #ccc; border-radius: 4px; }
    button { margin: 6px 4px 0 0; padding: 6px 8px; border: 1px solid #bbb; border-radius: 4px; background: #fff; cursor: pointer; }
    button:hover { background: #f0f0f0; }
    .control-row { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; margin-top: 6px; }
    .checkbox-row { display: flex; align-items: center; gap: 6px; margin-top: 8px; font-size: 13px; }
    .control { margin: 8px 0; font-size: 13px; }
    .control label { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 3px; }
    .control input[type="range"], .control select { width: 100%; box-sizing: border-box; }
    .value { color: #555; font-variant-numeric: tabular-nums; }
    pre { white-space: pre-wrap; word-break: break-word; background: #fff; border: 1px solid #ddd; padding: 8px; font-size: 11px; max-height: 260px; overflow: auto; }
    a { color: #2c7bb6; }
  </style>
</head>
<body>
  <div id="app">
    <aside id="panel">
      <h1>__TITLE__</h1>
      <p class="muted">Self-contained Canvas/WebGL-friendly view. Layout and filtering were computed with python-igraph, not NetworkX. No external browser libraries are required.</p>
      <div id="stats"></div>
      <h2>Search visible nodes</h2>
      <input id="search" type="search" placeholder="name, type, source id, UUID…" />
      <div class="control-row">
        <button id="resetCamera">Reset camera</button>
        <button id="fitVisible">Fit visible</button>
        <button id="clearSearch">Clear search</button>
        <button id="clearSelection">Clear selection</button>
        <button id="togglePhysics">Pause physics</button>
        <button id="unpinAll">Unpin all</button>
        <button id="resetAdjustments">Reset settings</button>
      </div>
      <label class="checkbox-row"><input id="highlightNeighbors" type="checkbox" checked /> Highlight selected node neighborhood</label>
      <p id="physicsStatus" class="muted"></p>
      <h2>Visual sizing</h2>
      <div class="control"><label for="nodeSizeMetric">Node size metric</label><select id="nodeSizeMetric"><option value="fixed">Fixed</option><option value="degree" selected>Degree</option><option value="betweenness">Betweenness centrality</option><option value="closeness">Closeness centrality</option><option value="pagerank">PageRank centrality</option></select></div>
      <div class="control"><label for="nodeSizeFactor">Node size modifier <span id="nodeSizeFactorValue" class="value"></span></label><input id="nodeSizeFactor" type="range" min="0.2" max="4" step="0.1" value="1"></div>
      <div class="control"><label for="edgeSizeMetric">Edge size metric</label><select id="edgeSizeMetric"><option value="fixed" selected>Fixed</option><option value="edge_betweenness">Edge betweenness centrality</option></select></div>
      <div class="control"><label for="edgeSizeFactor">Edge size modifier <span id="edgeSizeFactorValue" class="value"></span></label><input id="edgeSizeFactor" type="range" min="0.2" max="6" step="0.1" value="1"></div>
      <h2>Physics</h2>
      <div class="control"><label for="linkDistanceControl">Link distance <span id="linkDistanceValue" class="value"></span></label><input id="linkDistanceControl" type="range" min="4" max="120" step="1"></div>
      <div class="control"><label for="springStrengthControl">Link strength <span id="springStrengthValue" class="value"></span></label><input id="springStrengthControl" type="range" min="0" max="0.08" step="0.002"></div>
      <div class="control"><label for="repulsionStrengthControl">Repulsion strength <span id="repulsionStrengthValue" class="value"></span></label><input id="repulsionStrengthControl" type="range" min="0" max="2" step="0.05"></div>
      <div class="control"><label for="repulsionDistanceControl">Repulsion distance <span id="repulsionDistanceValue" class="value"></span></label><input id="repulsionDistanceControl" type="range" min="10" max="200" step="1"></div>
      <div class="control"><label for="centerStrengthControl">Center strength <span id="centerStrengthValue" class="value"></span></label><input id="centerStrengthControl" type="range" min="0" max="0.004" step="0.0001"></div>
      <div class="control"><label for="dampingControl">Damping <span id="dampingValue" class="value"></span></label><input id="dampingControl" type="range" min="0.6" max="0.98" step="0.01"></div>
      <h2>Node types</h2>
      <div id="types"></div>
      <h2>Selection</h2>
      <pre id="selection">Click a node to inspect it.</pre>
      <h2>Notes</h2>
      <p class="muted">Drag empty space to pan. Drag a node to reposition and pin it. Pinned nodes stay fixed while the rest of the graph responds to link, repulsion, and centering forces. Scroll/pinch to zoom. Hover nodes/edges for quick labels. Click a node or edge to inspect it; double-click a node to center it. For dense views, regenerate with <code>--max-nodes</code>, <code>--focus</code>, <code>--focus-depth</code>, <code>--types</code>, or <code>--largest-component</code>.</p>
    </aside>
    <main id="graphWrap"><canvas id="graph"></canvas><div id="tooltip"></div><div id="hud"></div></main>
  </div>
  <script id="graph-data" type="application/json">__PAYLOAD__</script>
  <script>
    const payload = JSON.parse(document.getElementById("graph-data").textContent);
    const canvas = document.getElementById("graph");
    const ctx = canvas.getContext("2d", { alpha: false });
    const wrap = document.getElementById("graphWrap");
    const hud = document.getElementById("hud");
    const tooltip = document.getElementById("tooltip");
    const physicsStatus = document.getElementById("physicsStatus");

    const nodes = payload.nodes.map(n => ({ id: n.key, ...n.attributes, visible: true, sx: 0, sy: 0, vx: 0, vy: 0, pinned: false }));
    const nodeById = new Map(nodes.map(n => [n.id, n]));
    const edges = payload.edges.map(e => ({ id: e.key, source: nodeById.get(e.source), target: nodeById.get(e.target), ...e.attributes, visible: true })).filter(e => e.source && e.target);
    const pinStorageKey = `aopkg-pins:${location.pathname}:${payload.meta.node_count}:${payload.meta.edge_count}`;
    try {
      const savedPins = JSON.parse(localStorage.getItem(pinStorageKey) || "{}");
      for (const n of nodes) {
        const pin = savedPins[n.id];
        if (pin && Number.isFinite(pin.x) && Number.isFinite(pin.y)) {
          n.x = pin.x; n.y = pin.y; n.pinned = true;
        }
      }
    } catch (_) {}
    const initialPositions = new Map(nodes.map(n => [n.id, { x: n.x, y: n.y }]));
    const selectedTypes = new Set(Object.keys(payload.meta.node_type_counts));
    const controls = {
      nodeSizeMetric: "degree",
      nodeSizeFactor: 1,
      edgeSizeMetric: "fixed",
      edgeSizeFactor: 1,
    };
    let selected = null;
    let selectedEdge = null;
    let hoverNode = null;
    let hoverEdge = null;
    let highlightNeighbors = true;
    let physicsEnabled = true;
    let simulationActive = false;
    let alpha = 0.8;
    let search = "";
    let dpr = window.devicePixelRatio || 1;
    let transform = { x: 0, y: 0, scale: 1 };
    let initialTransform = null;
    let needsDraw = true;

    function resize() {
      dpr = window.devicePixelRatio || 1;
      const rect = wrap.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      canvas.style.width = rect.width + "px";
      canvas.style.height = rect.height + "px";
      if (!initialTransform) fitToScreen();
      requestDraw();
    }

    function fitToScreen(visibleOnly = false, rememberInitial = true) {
      const candidates = visibleOnly ? nodes.filter(n => n.visible) : nodes;
      if (!candidates.length) return;
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const n of candidates) {
        minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
        minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
      }
      const rect = wrap.getBoundingClientRect();
      const width = Math.max(1, maxX - minX), height = Math.max(1, maxY - minY);
      const scale = 0.88 * Math.min(rect.width / width, rect.height / height);
      transform = { x: rect.width / 2 - scale * (minX + maxX) / 2, y: rect.height / 2 - scale * (minY + maxY) / 2, scale };
      if (rememberInitial) initialTransform = { ...transform };
    }

    function worldToScreen(n) {
      n.sx = transform.x + n.x * transform.scale;
      n.sy = transform.y + n.y * transform.scale;
    }

    function screenToWorld(clientX, clientY) {
      const rect = canvas.getBoundingClientRect();
      return { x: (clientX - rect.left - transform.x) / transform.scale, y: (clientY - rect.top - transform.y) / transform.scale };
    }

    function savePins() {
      try {
        const pins = {};
        for (const n of nodes) if (n.pinned) pins[n.id] = { x: n.x, y: n.y };
        localStorage.setItem(pinStorageKey, JSON.stringify(pins));
      } catch (_) {}
      updatePhysicsStatus();
    }

    function pinnedCount() {
      return nodes.reduce((count, n) => count + (n.pinned ? 1 : 0), 0);
    }

    function updatePhysicsStatus() {
      physicsStatus.textContent = `${physicsEnabled ? "Physics on" : "Physics paused"} · ${pinnedCount().toLocaleString()} pinned node(s)`;
    }

    function requestDraw() {
      if (!needsDraw) {
        needsDraw = true;
        requestAnimationFrame(draw);
      }
    }

    function matchesSearch(n) {
      if (!search) return true;
      const text = [n.label, n.name, n.type, n.kind, n.source, n.source_id, n.uri, n.modifiers, n.id].join(" ").toLowerCase();
      return text.includes(search);
    }

    function updateVisibility() {
      let visibleNodes = 0;
      for (const n of nodes) {
        n.visible = selectedTypes.has(n.type) && matchesSearch(n);
        if (n.visible) visibleNodes++;
      }
      let visibleEdges = 0;
      for (const e of edges) {
        e.visible = e.source.visible && e.target.visible;
        if (e.visible) visibleEdges++;
      }
      hud.textContent = `${visibleNodes.toLocaleString()} visible nodes · ${visibleEdges.toLocaleString()} visible edges`;
      if (selected && !selected.visible) showSelection(null, null, false);
      if (selectedEdge && !selectedEdge.visible) showSelection(null, null, false);
      wakeSimulation(0.2);
      requestDraw();
    }

    function selectedNeighborhood() {
      if (!selected || !highlightNeighbors) return { nodeIds: null, edgeSet: null };
      const nodeIds = new Set([selected.id]);
      const edgeSet = new Set();
      for (const e of edges) {
        if (!e.visible) continue;
        if (e.source === selected || e.target === selected) {
          nodeIds.add(e.source.id);
          nodeIds.add(e.target.id);
          edgeSet.add(e);
        }
      }
      return { nodeIds, edgeSet };
    }

    function maxMetric(items, metric) {
      let max = 0;
      for (const item of items) max = Math.max(max, Number(item[metric]) || 0);
      return max || 1;
    }

    function normalizedLog(value, max) {
      return Math.log1p(Math.max(0, Number(value) || 0)) / Math.log1p(max || 1);
    }

    function updateVisualSizes() {
      const nodeMetric = controls.nodeSizeMetric;
      const edgeMetric = controls.edgeSizeMetric;
      const nodeMax = nodeMetric === "fixed" ? 1 : maxMetric(nodes, nodeMetric);
      const edgeMax = edgeMetric === "fixed" ? 1 : maxMetric(edges, edgeMetric);
      for (const n of nodes) {
        const score = nodeMetric === "fixed" ? 0.5 : normalizedLog(n[nodeMetric], nodeMax);
        n.renderSize = Math.max(1, controls.nodeSizeFactor * (3 + 10 * score));
      }
      for (const e of edges) {
        const score = edgeMetric === "fixed" ? 0 : normalizedLog(e[edgeMetric], edgeMax);
        e.renderSize = Math.max(0.2, controls.edgeSizeFactor * (0.7 + 4.5 * score));
      }
      document.getElementById("nodeSizeFactorValue").textContent = `${controls.nodeSizeFactor.toFixed(1)}×`;
      document.getElementById("edgeSizeFactorValue").textContent = `${controls.edgeSizeFactor.toFixed(1)}×`;
      requestDraw();
    }

    function drawEdge(e, color, width, alpha) {
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.beginPath();
      ctx.moveTo(e.source.sx, e.source.sy);
      ctx.lineTo(e.target.sx, e.target.sy);
      ctx.stroke();
      ctx.restore();
    }

    function estimateLinkDistance() {
      const sample = [];
      const step = Math.max(1, Math.floor(edges.length / 800));
      for (let i = 0; i < edges.length; i += step) {
        const e = edges[i];
        sample.push(Math.hypot(e.target.x - e.source.x, e.target.y - e.source.y));
      }
      sample.sort((a, b) => a - b);
      const median = sample.length ? sample[Math.floor(sample.length / 2)] : 20;
      return Math.max(8, Math.min(45, median || 20));
    }

    const defaultPhysics = {
      linkDistance: estimateLinkDistance(),
      springStrength: 0.018,
      repulsionStrength: 0.35,
      repulsionDistance: 0,
      centerStrength: 0.0008,
      damping: 0.86,
      alphaDecay: 0.985,
    };
    defaultPhysics.repulsionDistance = defaultPhysics.linkDistance * 2.4;
    const physics = { ...defaultPhysics };

    function syncPhysicsControls() {
      document.getElementById("linkDistanceControl").value = physics.linkDistance;
      document.getElementById("springStrengthControl").value = physics.springStrength;
      document.getElementById("repulsionStrengthControl").value = physics.repulsionStrength;
      document.getElementById("repulsionDistanceControl").value = physics.repulsionDistance;
      document.getElementById("centerStrengthControl").value = physics.centerStrength;
      document.getElementById("dampingControl").value = physics.damping;
      updatePhysicsLabels();
    }

    function updatePhysicsLabels() {
      document.getElementById("linkDistanceValue").textContent = physics.linkDistance.toFixed(0);
      document.getElementById("springStrengthValue").textContent = physics.springStrength.toFixed(3);
      document.getElementById("repulsionStrengthValue").textContent = physics.repulsionStrength.toFixed(2);
      document.getElementById("repulsionDistanceValue").textContent = physics.repulsionDistance.toFixed(0);
      document.getElementById("centerStrengthValue").textContent = physics.centerStrength.toFixed(4);
      document.getElementById("dampingValue").textContent = physics.damping.toFixed(2);
    }

    function setPhysicsParam(name, value) {
      physics[name] = Number(value);
      updatePhysicsLabels();
      wakeSimulation(0.5);
    }

    function wakeSimulation(boost = 0.35) {
      if (!physicsEnabled) return;
      alpha = Math.max(alpha, boost);
      if (!simulationActive) {
        simulationActive = true;
        requestAnimationFrame(physicsTick);
      }
      updatePhysicsStatus();
    }

    function physicsTick() {
      if (!physicsEnabled) { simulationActive = false; updatePhysicsStatus(); return; }
      const visibleNodes = nodes.filter(n => n.visible);
      if (!visibleNodes.length) { simulationActive = false; return; }

      let cx = 0, cy = 0;
      for (const n of visibleNodes) { cx += n.x; cy += n.y; }
      cx /= visibleNodes.length; cy /= visibleNodes.length;

      const spring = physics.springStrength * alpha;
      for (const e of edges) {
        if (!e.visible) continue;
        const dx = e.target.x - e.source.x, dy = e.target.y - e.source.y;
        const dist = Math.max(0.001, Math.hypot(dx, dy));
        const force = (dist - physics.linkDistance) * spring;
        const fx = (dx / dist) * force, fy = (dy / dist) * force;
        if (!e.source.pinned) { e.source.vx += fx; e.source.vy += fy; }
        if (!e.target.pinned) { e.target.vx -= fx; e.target.vy -= fy; }
      }

      const repulsionDistance = Math.max(1, physics.repulsionDistance);
      const cellSize = repulsionDistance;
      const grid = new Map();
      for (const n of visibleNodes) {
        const gx = Math.floor(n.x / cellSize), gy = Math.floor(n.y / cellSize);
        const key = gx + "," + gy;
        if (!grid.has(key)) grid.set(key, []);
        grid.get(key).push(n);
      }
      const repel = physics.repulsionStrength * alpha;
      for (const n of visibleNodes) {
        const gx = Math.floor(n.x / cellSize), gy = Math.floor(n.y / cellSize);
        for (let ix = gx - 1; ix <= gx + 1; ix++) {
          for (let iy = gy - 1; iy <= gy + 1; iy++) {
            const bucket = grid.get(ix + "," + iy);
            if (!bucket) continue;
            for (const other of bucket) {
              if (other === n) continue;
              let dx = n.x - other.x, dy = n.y - other.y;
              let dist2 = dx * dx + dy * dy;
              if (dist2 <= 0.0001) { dx = (Math.random() - 0.5) * 0.01; dy = (Math.random() - 0.5) * 0.01; dist2 = dx * dx + dy * dy; }
              if (dist2 > repulsionDistance * repulsionDistance) continue;
              const dist = Math.sqrt(dist2);
              const force = repel * (1 - dist / repulsionDistance) / Math.max(1, dist);
              if (!n.pinned) { n.vx += dx * force; n.vy += dy * force; }
            }
          }
        }
      }

      const centerStrength = physics.centerStrength * alpha;
      for (const n of visibleNodes) {
        if (n.pinned) { n.vx = 0; n.vy = 0; continue; }
        n.vx += (cx - n.x) * centerStrength;
        n.vy += (cy - n.y) * centerStrength;
        n.vx *= physics.damping;
        n.vy *= physics.damping;
        n.x += Math.max(-8, Math.min(8, n.vx));
        n.y += Math.max(-8, Math.min(8, n.vy));
      }

      alpha *= physics.alphaDecay;
      requestDraw();
      if (alpha > 0.012) requestAnimationFrame(physicsTick); else { simulationActive = false; updatePhysicsStatus(); }
    }

    function draw() {
      needsDraw = false;
      const w = canvas.width / dpr, h = canvas.height / dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, w, h);
      for (const n of nodes) worldToScreen(n);

      const { nodeIds: neighborIds, edgeSet: neighborEdges } = selectedNeighborhood();
      const baseEdgeAlpha = Math.max(0.08, Math.min(0.45, 0.16 + transform.scale / 260));
      for (const e of edges) {
        if (!e.visible) continue;
        const isSelected = selectedEdge === e;
        const isNeighbor = neighborEdges && neighborEdges.has(e);
        if (isSelected || isNeighbor) continue;
        drawEdge(e, "#8a8a8a", e.renderSize || 0.7, neighborEdges ? 0.07 : baseEdgeAlpha);
      }
      for (const e of edges) {
        if (!e.visible) continue;
        if (neighborEdges && neighborEdges.has(e)) drawEdge(e, "#222", Math.max(1.8, (e.renderSize || 0.7) + 1), 0.8);
      }
      if (selectedEdge && selectedEdge.visible) drawEdge(selectedEdge, "#d7191c", Math.max(2.8, (selectedEdge.renderSize || 0.7) + 1.5), 0.95);
      if (hoverEdge && hoverEdge.visible && hoverEdge !== selectedEdge) drawEdge(hoverEdge, "#2c7bb6", Math.max(2.0, (hoverEdge.renderSize || 0.7) + 1), 0.8);

      for (const n of nodes) {
        if (!n.visible) continue;
        const isSelected = selected && selected.id === n.id;
        const isHovered = hoverNode && hoverNode.id === n.id;
        const isNeighbor = !neighborIds || neighborIds.has(n.id);
        const r = Math.max(1.0, Math.min(24, (n.renderSize || n.size || 5) * Math.sqrt(Math.max(transform.scale, 0.001))));
        ctx.save();
        ctx.globalAlpha = isNeighbor ? 1 : 0.18;
        ctx.beginPath();
        ctx.fillStyle = n.color || "#888";
        ctx.arc(n.sx, n.sy, isHovered ? r + 2 : r, 0, Math.PI * 2);
        ctx.fill();
        if (isSelected || isHovered || n.pinned) {
          ctx.lineWidth = isSelected ? 3 : 2;
          ctx.strokeStyle = isSelected ? "#000" : (n.pinned ? "#444" : "#2c7bb6");
          ctx.stroke();
        }
        if (n.pinned) {
          ctx.fillStyle = "#111";
          ctx.fillRect(n.sx - 2, n.sy - r - 6, 4, 4);
        }
        ctx.restore();
      }

      if (transform.scale > 8 || selected || hoverNode) {
        ctx.font = "12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
        ctx.fillStyle = "#222";
        for (const n of nodes) {
          if (!n.visible) continue;
          const shouldLabel =
            (hoverNode && n === hoverNode) ||
            (selected && neighborIds && neighborIds.has(n.id)) ||
            (!hoverNode && !selected && transform.scale > 8 && (n.renderSize || n.size || 0) >= 6);
          if (!shouldLabel) continue;
          ctx.fillText(n.label || n.id, n.sx + 7, n.sy - 7);
        }
      }
    }

    function nearestNode(clientX, clientY) {
      const rect = canvas.getBoundingClientRect();
      const x = clientX - rect.left, y = clientY - rect.top;
      let best = null, bestDist = Infinity;
      for (const n of nodes) {
        if (!n.visible) continue;
        const dx = n.sx - x, dy = n.sy - y;
        const dist = dx * dx + dy * dy;
        const radius = Math.max(8, (n.renderSize || n.size || 5) * Math.sqrt(Math.max(transform.scale, 0.001)) + 4);
        if (dist < radius * radius && dist < bestDist) { best = n; bestDist = dist; }
      }
      return best;
    }

    function distanceToSegmentSquared(px, py, ax, ay, bx, by) {
      const dx = bx - ax, dy = by - ay;
      if (dx === 0 && dy === 0) return (px - ax) ** 2 + (py - ay) ** 2;
      const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
      const x = ax + t * dx, y = ay + t * dy;
      return (px - x) ** 2 + (py - y) ** 2;
    }

    function nearestEdge(clientX, clientY) {
      const rect = canvas.getBoundingClientRect();
      const x = clientX - rect.left, y = clientY - rect.top;
      let best = null, bestDist = Infinity;
      for (const e of edges) {
        if (!e.visible) continue;
        const dist = distanceToSegmentSquared(x, y, e.source.sx, e.source.sy, e.target.sx, e.target.sy);
        if (dist < 36 && dist < bestDist) { best = e; bestDist = dist; }
      }
      return best;
    }

    function hideTooltip() {
      tooltip.style.display = "none";
    }

    function showTooltip(clientX, clientY, lines) {
      tooltip.innerHTML = lines.map(line => String(line).replace(/[&<>]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[ch]))).join("<br>");
      const rect = wrap.getBoundingClientRect();
      tooltip.style.left = Math.min(clientX - rect.left + 12, rect.width - 20) + "px";
      tooltip.style.top = Math.min(clientY - rect.top + 12, rect.height - 20) + "px";
      tooltip.style.display = "block";
    }

    function edgeSummary(e) {
      return { id: e.id, label: e.label, type: e.type, source: e.source.id, source_label: e.source.label, target: e.target.id, target_label: e.target.label, edge_betweenness: e.edge_betweenness, rendered_size: e.renderSize, properties: e.properties };
    }

    function showSelection(node, edge, redraw = true) {
      selected = node;
      selectedEdge = edge;
      if (!node && !edge) {
        document.getElementById("selection").textContent = "Click a node or edge to inspect it.";
      } else if (node) {
        let neighborCount = 0;
        const incidentEdges = [];
        for (const e of edges) {
          if (e.source === node || e.target === node) {
            neighborCount++;
            if (incidentEdges.length < 12) incidentEdges.push(`${e.source.label || e.source.id} → ${e.target.label || e.target.id} (${e.type || e.label || "edge"})`);
          }
        }
        document.getElementById("selection").textContent = JSON.stringify({ id: node.id, label: node.label, type: node.type, kind: node.kind, source: node.source, source_id: node.source_id, uri: node.uri, degree: node.degree, betweenness: node.betweenness, closeness: node.closeness, pagerank: node.pagerank, rendered_size: node.renderSize, pinned: node.pinned, edge_count: neighborCount, sample_edges: incidentEdges }, null, 2);
      } else {
        document.getElementById("selection").textContent = JSON.stringify(edgeSummary(edge), null, 2);
      }
      if (redraw) requestDraw();
    }

    function centerOnNode(n, targetScale = Math.max(transform.scale * 1.8, 18)) {
      const rect = wrap.getBoundingClientRect();
      transform.scale = Math.min(1000, targetScale);
      transform.x = rect.width / 2 - n.x * transform.scale;
      transform.y = rect.height / 2 - n.y * transform.scale;
      requestDraw();
    }

    function renderStats() {
      document.getElementById("stats").innerHTML = `
        <div class="stat"><b>Nodes</b><span>${payload.meta.node_count.toLocaleString()}</span></div>
        <div class="stat"><b>Edges</b><span>${payload.meta.edge_count.toLocaleString()}</span></div>`;
    }

    function renderTypes() {
      const target = document.getElementById("types");
      const typeEntries = Object.entries(payload.meta.node_type_counts).sort((a, b) => b[1] - a[1]);
      target.innerHTML = "";
      for (const [type, count] of typeEntries) {
        const sample = nodes.find(n => n.type === type);
        const row = document.createElement("label");
        row.className = "type-row";
        row.innerHTML = `<input type="checkbox" checked data-type="${type}"><span class="swatch" style="background:${sample ? sample.color : '#999'}"></span><span>${type}</span><span class="muted">${count}</span>`;
        target.appendChild(row);
      }
      target.addEventListener("change", (event) => {
        const type = event.target.dataset && event.target.dataset.type;
        if (!type) return;
        if (event.target.checked) selectedTypes.add(type); else selectedTypes.delete(type);
        updateVisibility();
      });
    }

    let draggingCanvas = false, draggingNode = null, moved = false, last = null;
    canvas.addEventListener("pointerdown", e => {
      moved = false;
      last = { x: e.clientX, y: e.clientY };
      hideTooltip();
      for (const n of nodes) worldToScreen(n);
      draggingNode = nearestNode(e.clientX, e.clientY);
      draggingCanvas = !draggingNode;
      canvas.style.cursor = draggingNode ? "grabbing" : "grab";
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener("pointermove", e => {
      if (draggingNode) {
        const dx = e.clientX - last.x, dy = e.clientY - last.y;
        if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
        if (moved) {
          const point = screenToWorld(e.clientX, e.clientY);
          draggingNode.x = point.x;
          draggingNode.y = point.y;
          draggingNode.vx = 0;
          draggingNode.vy = 0;
          draggingNode.pinned = true;
          selected = draggingNode;
          selectedEdge = null;
          wakeSimulation(0.55);
        }
        last = { x: e.clientX, y: e.clientY };
        requestDraw();
        return;
      }
      if (draggingCanvas) {
        const dx = e.clientX - last.x, dy = e.clientY - last.y;
        if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
        transform.x += dx; transform.y += dy; last = { x: e.clientX, y: e.clientY };
        requestDraw();
        return;
      }
      hoverNode = nearestNode(e.clientX, e.clientY);
      hoverEdge = hoverNode ? null : nearestEdge(e.clientX, e.clientY);
      canvas.style.cursor = hoverNode || hoverEdge ? "pointer" : "grab";
      if (hoverNode) {
        showTooltip(e.clientX, e.clientY, [hoverNode.label || hoverNode.id, hoverNode.type, hoverNode.source_id || hoverNode.id, hoverNode.pinned ? "Pinned" : "Drag to pin"]);
      } else if (hoverEdge) {
        showTooltip(e.clientX, e.clientY, [`${hoverEdge.source.label || hoverEdge.source.id} → ${hoverEdge.target.label || hoverEdge.target.id}`, hoverEdge.type || hoverEdge.label || "edge"]);
      } else {
        hideTooltip();
      }
      requestDraw();
    });
    canvas.addEventListener("pointerleave", () => { if (!draggingNode && !draggingCanvas) { hoverNode = null; hoverEdge = null; hideTooltip(); requestDraw(); } });
    canvas.addEventListener("pointerup", e => {
      const node = draggingNode;
      draggingNode = null;
      draggingCanvas = false;
      canvas.style.cursor = "grab";
      if (node && moved) {
        savePins();
        showSelection(node, null);
      } else if (!moved) {
        const n = nearestNode(e.clientX, e.clientY);
        showSelection(n, n ? null : nearestEdge(e.clientX, e.clientY));
      }
    });
    canvas.addEventListener("dblclick", e => {
      const n = nearestNode(e.clientX, e.clientY);
      if (n) { showSelection(n, null); centerOnNode(n); }
    });
    canvas.addEventListener("wheel", e => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const factor = Math.exp(-e.deltaY * 0.001);
      const oldScale = transform.scale;
      const newScale = Math.max(0.01, Math.min(1000, oldScale * factor));
      const wx = (mx - transform.x) / oldScale;
      const wy = (my - transform.y) / oldScale;
      transform.scale = newScale;
      transform.x = mx - wx * newScale;
      transform.y = my - wy * newScale;
      requestDraw();
    }, { passive: false });

    document.getElementById("nodeSizeMetric").addEventListener("change", e => { controls.nodeSizeMetric = e.target.value; updateVisualSizes(); });
    document.getElementById("nodeSizeFactor").addEventListener("input", e => { controls.nodeSizeFactor = Number(e.target.value); updateVisualSizes(); });
    document.getElementById("edgeSizeMetric").addEventListener("change", e => { controls.edgeSizeMetric = e.target.value; updateVisualSizes(); });
    document.getElementById("edgeSizeFactor").addEventListener("input", e => { controls.edgeSizeFactor = Number(e.target.value); updateVisualSizes(); });
    document.getElementById("linkDistanceControl").addEventListener("input", e => setPhysicsParam("linkDistance", e.target.value));
    document.getElementById("springStrengthControl").addEventListener("input", e => setPhysicsParam("springStrength", e.target.value));
    document.getElementById("repulsionStrengthControl").addEventListener("input", e => setPhysicsParam("repulsionStrength", e.target.value));
    document.getElementById("repulsionDistanceControl").addEventListener("input", e => setPhysicsParam("repulsionDistance", e.target.value));
    document.getElementById("centerStrengthControl").addEventListener("input", e => setPhysicsParam("centerStrength", e.target.value));
    document.getElementById("dampingControl").addEventListener("input", e => setPhysicsParam("damping", e.target.value));

    document.getElementById("search").addEventListener("input", e => { search = e.target.value.trim().toLowerCase(); updateVisibility(); });
    document.getElementById("clearSearch").addEventListener("click", () => { document.getElementById("search").value = ""; search = ""; updateVisibility(); });
    document.getElementById("clearSelection").addEventListener("click", () => showSelection(null, null));
    document.getElementById("fitVisible").addEventListener("click", () => { fitToScreen(true, false); requestDraw(); });
    document.getElementById("highlightNeighbors").addEventListener("change", e => { highlightNeighbors = e.target.checked; requestDraw(); });
    document.getElementById("togglePhysics").addEventListener("click", e => {
      physicsEnabled = !physicsEnabled;
      e.target.textContent = physicsEnabled ? "Pause physics" : "Resume physics";
      updatePhysicsStatus();
      if (physicsEnabled) wakeSimulation(0.45);
    });
    document.getElementById("unpinAll").addEventListener("click", () => {
      for (const n of nodes) { n.pinned = false; n.vx = 0; n.vy = 0; }
      savePins();
      wakeSimulation(0.55);
      requestDraw();
    });
    document.getElementById("resetAdjustments").addEventListener("click", () => {
      Object.assign(controls, { nodeSizeMetric: "degree", nodeSizeFactor: 1, edgeSizeMetric: "fixed", edgeSizeFactor: 1 });
      Object.assign(physics, defaultPhysics);
      document.getElementById("nodeSizeMetric").value = controls.nodeSizeMetric;
      document.getElementById("nodeSizeFactor").value = controls.nodeSizeFactor;
      document.getElementById("edgeSizeMetric").value = controls.edgeSizeMetric;
      document.getElementById("edgeSizeFactor").value = controls.edgeSizeFactor;
      syncPhysicsControls();
      updateVisualSizes();
      wakeSimulation(0.65);
    });
    document.getElementById("resetCamera").addEventListener("click", () => { transform = { ...initialTransform }; showSelection(null, null); updateVisibility(); });
    window.addEventListener("resize", resize);

    renderStats();
    renderTypes();
    syncPhysicsControls();
    updateVisualSizes();
    resize();
    updateVisibility();
    updatePhysicsStatus();
    wakeSimulation(0.45);
    draw();
  </script>
</body>
</html>
"""

def write_html(payload: dict[str, Any], output: Path, title: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload_json = (
        json.dumps(payload, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    page = HTML_TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__PAYLOAD__", payload_json)
    output.write_text(page, encoding="utf-8")


def comma_set(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {item.strip() for item in value.split(",") if item.strip()}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Visualize converted AOP-Wiki nodes.csv/edges.csv as a self-contained interactive Canvas graph.")
    parser.add_argument("--nodes", type=Path, default=Path("data/aopwiki/converted/nodes.csv"), help="Converted nodes.csv path")
    parser.add_argument("--edges", type=Path, default=Path("data/aopwiki/converted/edges.csv"), help="Converted edges.csv path")
    parser.add_argument("-o", "--output", type=Path, default=Path("data/aopwiki/converted/aopwiki_graph.html"), help="Output HTML file")
    parser.add_argument("--title", default="AOP-Wiki Knowledge Graph", help="HTML page title")
    parser.add_argument("--layout", default="auto", choices=["auto", "drl", "fr", "kk", "circle", "lgl"], help="igraph layout algorithm. auto uses DRL for large graphs and FR for smaller graphs")
    parser.add_argument("--max-nodes", type=int, default=2500, help="Keep the top N nodes by degree after other filters; use 0 for all nodes")
    parser.add_argument("--types", help="Comma-separated node types to include, e.g. aop,key-event,stressor")
    parser.add_argument("--largest-component", action="store_true", help="Keep only the largest weakly connected component")
    parser.add_argument("--show-relationship-nodes", action="store_true", help="Show key-event-relationship records as separate nodes. By default they are represented only as direct KEY_EVENT_RELATIONSHIP edges.")
    parser.add_argument("--focus", help="Node UUID or substring to visualize with its neighborhood")
    parser.add_argument("--focus-depth", type=int, default=2, help="Neighborhood depth when --focus is used")
    return parser


def main(argv: list[str] | None = None) -> int:
    raise_csv_field_limit()
    args = build_arg_parser().parse_args(argv)
    nodes = read_nodes(args.nodes)
    edges = read_edges(args.edges, set(nodes))
    if not args.show_relationship_nodes:
        nodes, edges = hide_relationship_nodes(nodes, edges)
    graph, id_to_index = build_graph(nodes, edges)

    filtered_nodes, filtered_edges = filter_graph(
        graph,
        nodes,
        edges,
        id_to_index,
        node_types=comma_set(args.types),
        largest_component_only=args.largest_component,
        focus=args.focus,
        focus_depth=args.focus_depth,
        max_nodes=args.max_nodes,
    )
    payload = graph_payload(filtered_nodes, filtered_edges, args.layout)
    write_html(payload, args.output, args.title)

    print(f"Wrote {args.output}")
    print(f"  nodes: {payload['meta']['node_count']:,} / {len(nodes):,}")
    print(f"  edges: {payload['meta']['edge_count']:,} / {len(edges):,}")
    print("Open the file directly in a browser; no internet/CDN access is required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
