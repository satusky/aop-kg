# AOP-KG: Knowledge graph tools for Adverse Outcome Pathways

Adverse Outcome Pathways (AOPs) are a knowledge framework linking a Molecular Initiating Event (MIE) to an Adverse Outcome (AO) through a series of Key Events (KEs). Biological, chemical, and enviromental concepts compose each element in the AOP and are linked through directional relationships, which is exactly the same thing as a knowledge graph (KG).

## Overview
Rather than building a KG from scratch, it's easier to connect to existing ones. 

### Data sources
This repository contains some tools for aggregating established AOPs from source databases:
- [AOPWiki](https://aopwiki.org/)
- More to come...

### Concept normalization

To ensure conceptual elements (biological, chemical, environmental, etc.) are not duplicated, they must be linked to ontological identifiers. Most data sources contain identifiers that we can leverage.

### KG integration

More to come...


## Installation

Using `uv` is recommended.

Install the package into the current environment:

```bash
uv pip install .
```

For development, install in editable mode:

```bash
uv pip install -e .
```

## AOP-Wiki XML conversion

The converter is based on the AopXml 2.7.0 schema and writes both normalized JSON entity files and graph-friendly CSV tables. Entity JSON files are objects keyed by each record's `id`; `vendor_specific.jsonl` is kept as JSONL because it is export metadata rather than a primary entity table.

```bash
# Optional: keep a local copy of the schema used by the converter
mkdir -p schema
curl -L https://raw.githubusercontent.com/swandle06/AopXml/2.7.0/assets/schema/current.xsd \
  -o schema/aopxml-current-2.7.0.xsd

# Convert the April 2026 AOP-Wiki export
uv run aopwiki-convert data/aopwiki/xml/aop-wiki-xml-2026-04-01.xml -o data/aopwiki/converted
```

Outputs include `nodes.csv`, `edges.csv`, `summary.json`, id-keyed JSON files for AOPs, key events, key-event relationships, stressors, chemicals, taxonomies, and biological terms, plus `vendor_specific.jsonl`. In the graph CSVs, key-event relationships are direct `KEY_EVENT_RELATIONSHIP` edges between upstream/downstream key events, not nodes. Stressors are treated as modifiers/designations of molecular entities: if a stressor references an existing chemical, no separate stressor node is created; instead the chemical node has `"stressor"` in its `modifiers` attribute and `HAS_STRESSOR` edges point to that chemical. A standalone stressor node is only created when no referenced chemical entity exists.

## AOP-Wiki graph visualization

The visualizer avoids NetworkX. It uses `python-igraph` for fast graph filtering/layout and writes a self-contained interactive Canvas HTML file for browser rendering. Key-event relationships are rendered as direct `KEY_EVENT_RELATIONSHIP` edges between key events, not as separate nodes.

```bash
# Create an interactive HTML graph using the default converted CSV paths
uv run aopwiki-visualize -o data/aopwiki/converted/aopwiki_graph.html

# Useful focused view around one AOP/key event/stressor/etc.
uv run aopwiki-visualize \
  --focus e42dd460-65e3-4042-b0d9-7c1d3d1f7248 \
  --focus-depth 2 \
  -o data/aopwiki/converted/aopwiki_focus.html

# Include every non-KER node; default is top 2500 by degree for browser performance
uv run aopwiki-visualize --max-nodes 0 -o data/aopwiki/converted/aopwiki_graph_all.html

```

Open the generated HTML file directly in a browser. No internet/CDN access is required. The graph supports pan/zoom, node-type filtering, text search, node and edge hover tooltips, click-to-inspect, selected-neighborhood highlighting, double-click-to-center nodes, adjustable node/edge sizing by degree/centrality, and a browser-side force simulation with adjustable physics parameters. Drag a node to reposition it; dragged nodes are pinned to their current coordinates while the remaining visible nodes continue reacting to link, repulsion, and centering forces. Use “Unpin all” to release pinned nodes and “Reset settings” to restore sizing/physics defaults.
