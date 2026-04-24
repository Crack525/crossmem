"""Knowledge graph visualization for crossmem."""

import http.server
import json
import threading
import webbrowser
from collections import defaultdict

from crossmem.store import MemoryStore

_STOP_WORDS = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "can",
    "shall",
    "to",
    "of",
    "in",
    "for",
    "on",
    "with",
    "at",
    "by",
    "from",
    "as",
    "into",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "between",
    "out",
    "off",
    "over",
    "under",
    "again",
    "further",
    "then",
    "once",
    "and",
    "but",
    "or",
    "nor",
    "not",
    "no",
    "so",
    "if",
    "that",
    "this",
    "these",
    "those",
    "it",
    "its",
    "all",
    "each",
    "every",
    "both",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "only",
    "own",
    "same",
    "than",
    "too",
    "very",
    "just",
    "about",
    "use",
    "used",
    "using",
    "also",
    "new",
    "one",
    "two",
    "first",
    "last",
    "file",
    "files",
    "run",
    "set",
    "get",
    "add",
    "see",
    "e",
    "g",
}


def build_graph_data(store: MemoryStore) -> dict:
    """Build nodes and edges for the knowledge graph.

    Nodes: projects and their sections.
    Edges: project→section ownership + cross-project links via shared sections.
    """
    rows = store.db.execute(
        "SELECT id, content, source_file, project, section FROM memories"
    ).fetchall()

    projects: dict[str, int] = defaultdict(int)
    sections: dict[str, set[str]] = defaultdict(set)  # section_name → {projects}
    project_sections: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for row in rows:
        project = row["project"]
        section = row["section"] or "(root)"
        projects[project] += 1
        sections[section].add(project)
        project_sections[project][section] += 1

    nodes = []
    edges = []
    node_ids: dict[str, int] = {}

    # Project nodes
    for i, (project, count) in enumerate(sorted(projects.items(), key=lambda x: -x[1])):
        node_ids[f"proj:{project}"] = i
        nodes.append(
            {
                "id": i,
                "label": project,
                "type": "project",
                "size": count,
            }
        )

    # Section nodes (only sections that appear in 1+ project)
    for section, projs in sorted(sections.items(), key=lambda x: -len(x[1])):
        if section == "(root)" and len(projs) == 1:
            continue
        idx = len(nodes)
        node_ids[f"sec:{section}"] = idx
        nodes.append(
            {
                "id": idx,
                "label": section,
                "type": "shared_section" if len(projs) > 1 else "section",
                "size": sum(project_sections[p][section] for p in projs),
                "projects": sorted(projs),
            }
        )

        # Edges from section to each project
        for proj in projs:
            proj_key = f"proj:{proj}"
            if proj_key in node_ids:
                edges.append(
                    {
                        "source": node_ids[proj_key],
                        "target": idx,
                        "weight": project_sections[proj][section],
                    }
                )

    # Cross-project edges via shared keywords in content
    project_keywords: dict[str, set[str]] = defaultdict(set)
    stop_words = _STOP_WORDS
    for row in rows:
        words = set(
            w.lower().strip("`*_-()[]{}.,;:!?\"'#/\\") for w in row["content"].split() if len(w) > 2
        )
        words -= stop_words
        project_keywords[row["project"]].update(words)

    # Find keyword overlap between project pairs
    proj_list = sorted(projects.keys())
    for i, p1 in enumerate(proj_list):
        for p2 in proj_list[i + 1 :]:
            shared = project_keywords[p1] & project_keywords[p2]
            if len(shared) > 5:  # meaningful overlap
                k1 = f"proj:{p1}"
                k2 = f"proj:{p2}"
                if k1 in node_ids and k2 in node_ids:
                    edges.append(
                        {
                            "source": node_ids[k1],
                            "target": node_ids[k2],
                            "weight": len(shared),
                            "type": "keyword_overlap",
                            "shared_keywords": sorted(shared)[:20],
                        }
                    )

    return {"nodes": nodes, "edges": edges}


HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>crossmem — Knowledge Graph</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0a0a0f;
    color: #e0e0e0;
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    overflow: hidden;
  }
  #header {
    position: fixed; top: 0; left: 0; right: 0; z-index: 10;
    padding: 16px 24px;
    background: linear-gradient(180deg, rgba(10,10,15,0.95) 0%, rgba(10,10,15,0) 100%);
    display: flex; align-items: center; gap: 16px;
  }
  #header h1 { font-size: 18px; color: #7aa2f7; font-weight: 600; }
  #header .stats { font-size: 13px; color: #565f89; }
  #legend {
    position: fixed; bottom: 20px; left: 20px; z-index: 10;
    background: rgba(20, 20, 30, 0.9);
    border: 1px solid #1a1b26;
    border-radius: 8px; padding: 12px 16px;
    font-size: 12px;
  }
  .legend-item { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
  .legend-dot { width: 10px; height: 10px; border-radius: 50%; }
  #tooltip {
    position: fixed; z-index: 20;
    background: rgba(20, 20, 30, 0.95);
    border: 1px solid #3d59a1;
    border-radius: 8px; padding: 12px 16px;
    font-size: 12px; max-width: 320px;
    pointer-events: none; display: none;
    box-shadow: 0 4px 20px rgba(0,0,0,0.5);
  }
  #tooltip h3 { color: #7aa2f7; margin-bottom: 6px; font-size: 14px; }
  #tooltip .detail { color: #9aa5ce; margin: 2px 0; }
  #tooltip .keywords { color: #73daca; margin-top: 6px; font-size: 11px; word-break: break-word; }
  svg { width: 100vw; height: 100vh; }
  .link { stroke-opacity: 0.3; }
  .link-keyword { stroke: #3d59a1; stroke-dasharray: 4,4; }
  .link-section { stroke: #1a1b26; }
  .node-label {
    fill: #c0caf5; font-size: 11px;
    text-anchor: middle; pointer-events: none;
    text-shadow: 0 0 8px rgba(10,10,15,0.9), 0 0 4px rgba(10,10,15,0.9);
  }
</style>
</head>
<body>
<div id="header">
  <h1>crossmem</h1>
  <span class="stats" id="stats"></span>
</div>
<div id="legend">
  <div class="legend-item"><div class="legend-dot" style="background:#7aa2f7"></div> Project</div>
  <div class="legend-item"><div class="legend-dot" style="background:#ff9e64"></div> Shared section (multi-project)</div>
  <div class="legend-item"><div class="legend-dot" style="background:#565f89"></div> Section</div>
  <div class="legend-item"><svg width="30" height="10"><line x1="0" y1="5" x2="30" y2="5" stroke="#3d59a1" stroke-dasharray="4,4" stroke-width="1.5"/></svg> Keyword overlap</div>
</div>
<div id="tooltip"></div>
<svg></svg>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
const data = __GRAPH_DATA__;

const statsEl = document.getElementById('stats');
const projects = data.nodes.filter(n => n.type === 'project');
const totalMem = projects.reduce((s, n) => s + n.size, 0);
const shared = data.nodes.filter(n => n.type === 'shared_section').length;
statsEl.textContent = `${totalMem} memories | ${projects.length} projects | ${shared} shared sections`;

const svg = d3.select('svg');
const width = window.innerWidth;
const height = window.innerHeight;

const g = svg.append('g');

// Zoom
svg.call(d3.zoom()
  .scaleExtent([0.2, 5])
  .on('zoom', (e) => g.attr('transform', e.transform)));

const color = d => ({
  project: '#7aa2f7',
  shared_section: '#ff9e64',
  section: '#565f89',
}[d.type] || '#565f89');

const radius = d => {
  if (d.type === 'project') return Math.max(12, Math.sqrt(d.size) * 3);
  if (d.type === 'shared_section') return Math.max(8, Math.sqrt(d.size) * 2.5);
  return Math.max(5, Math.sqrt(d.size) * 2);
};

const sim = d3.forceSimulation(data.nodes)
  .force('link', d3.forceLink(data.edges).id(d => d.id).distance(d =>
    d.type === 'keyword_overlap' ? 150 : 60
  ).strength(d => d.type === 'keyword_overlap' ? 0.1 : 0.3))
  .force('charge', d3.forceManyBody().strength(d =>
    d.type === 'project' ? -300 : -80
  ))
  .force('center', d3.forceCenter(width / 2, height / 2))
  .force('collision', d3.forceCollide().radius(d => radius(d) + 4));

const link = g.selectAll('.link')
  .data(data.edges).enter().append('line')
  .attr('class', d => 'link ' + (d.type === 'keyword_overlap' ? 'link-keyword' : 'link-section'))
  .attr('stroke-width', d => d.type === 'keyword_overlap'
    ? Math.min(3, Math.sqrt(d.weight) * 0.3)
    : Math.min(2, d.weight * 0.5));

const node = g.selectAll('.node')
  .data(data.nodes).enter().append('circle')
  .attr('r', radius)
  .attr('fill', color)
  .attr('stroke', d => d.type === 'project' ? '#7aa2f7' : 'none')
  .attr('stroke-width', d => d.type === 'project' ? 2 : 0)
  .attr('fill-opacity', d => d.type === 'project' ? 0.85 : 0.6)
  .attr('cursor', 'pointer')
  .call(d3.drag()
    .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
    .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
    .on('end', (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

const label = g.selectAll('.node-label')
  .data(data.nodes.filter(n => n.type === 'project' || n.type === 'shared_section'))
  .enter().append('text')
  .attr('class', 'node-label')
  .attr('dy', d => radius(d) + 14)
  .text(d => d.label);

// Tooltip
const tooltip = document.getElementById('tooltip');

node.on('mouseover', (e, d) => {
  let html = `<h3>${d.label}</h3>`;
  html += `<div class="detail">Type: ${d.type.replace('_', ' ')}</div>`;
  html += `<div class="detail">Memories: ${d.size}</div>`;
  if (d.projects) html += `<div class="detail">In: ${d.projects.join(', ')}</div>`;

  // Find keyword overlaps for this node
  const overlaps = data.edges.filter(e =>
    e.type === 'keyword_overlap' &&
    (e.source.id === d.id || e.target.id === d.id)
  );
  if (overlaps.length > 0) {
    overlaps.forEach(o => {
      const other = o.source.id === d.id ? o.target : o.source;
      html += `<div class="keywords">${o.shared_keywords.slice(0, 10).join(', ')} (shared with ${other.label})</div>`;
    });
  }
  tooltip.innerHTML = html;
  tooltip.style.display = 'block';
  tooltip.style.left = (e.pageX + 12) + 'px';
  tooltip.style.top = (e.pageY - 10) + 'px';

  // Highlight connected
  const connected = new Set();
  data.edges.forEach(e => {
    const sid = typeof e.source === 'object' ? e.source.id : e.source;
    const tid = typeof e.target === 'object' ? e.target.id : e.target;
    if (sid === d.id) connected.add(tid);
    if (tid === d.id) connected.add(sid);
  });
  node.attr('fill-opacity', n => n.id === d.id || connected.has(n.id) ? 1 : 0.15);
  link.attr('stroke-opacity', e => {
    const sid = typeof e.source === 'object' ? e.source.id : e.source;
    const tid = typeof e.target === 'object' ? e.target.id : e.target;
    return sid === d.id || tid === d.id ? 0.8 : 0.05;
  });
  label.attr('fill-opacity', n => n.id === d.id || connected.has(n.id) ? 1 : 0.15);
});

node.on('mousemove', (e) => {
  tooltip.style.left = (e.pageX + 12) + 'px';
  tooltip.style.top = (e.pageY - 10) + 'px';
});

node.on('mouseout', () => {
  tooltip.style.display = 'none';
  node.attr('fill-opacity', d => d.type === 'project' ? 0.85 : 0.6);
  link.attr('stroke-opacity', 0.3);
  label.attr('fill-opacity', 1);
});

sim.on('tick', () => {
  link
    .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
    .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
  node.attr('cx', d => d.x).attr('cy', d => d.y);
  label.attr('x', d => d.x).attr('y', d => d.y);
});
</script>
</body>
</html>"""


def serve_graph(store: MemoryStore, port: int = 8765) -> None:
    """Build graph data and serve the visualization."""
    graph_data = build_graph_data(store)
    store.close()
    safe_json = json.dumps(graph_data).replace("</", r"<\/")
    html = HTML_TEMPLATE.replace("__GRAPH_DATA__", safe_json)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())

        def log_message(self, format, *args) -> None:
            pass  # suppress access logs

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Knowledge graph: {url}")
    print("Press Ctrl+C to stop.")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()
