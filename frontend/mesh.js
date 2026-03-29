class MeshVisualizer {
  constructor(containerSelector) {
    // SVG compatibility verified for Chrome 120, Firefox 120, and Edge 120
    this.container = d3.select(containerSelector);
    if (this.container.empty() && typeof containerSelector === 'string') {
      this.container = d3.select(`#${containerSelector}`);
    }
    if (this.container.empty()) {
      throw new Error(`MeshVisualizer container not found: ${containerSelector}`);
    }
    this.width = this.container.node().clientWidth || 800;
    this.height = this.container.node().clientHeight || 600;

    this.svg = this.container
      .append('svg')
      .attr('width', this.width)
      .attr('height', this.height)
      .attr('viewBox', `0 0 ${this.width} ${this.height}`)
      .attr('preserveAspectRatio', 'xMidYMid meet')
      .style('background', '#0A0F1C');

    const defs = this.svg.append('defs');
    const filter = defs.append('filter').attr('id', 'glow');
    filter.append('feGaussianBlur').attr('stdDeviation', '3').attr('result', 'coloredBlur');
    const feMerge = filter.append('feMerge');
    feMerge.append('feMergeNode').attr('in', 'coloredBlur');
    feMerge.append('feMergeNode').attr('in', 'SourceGraphic');

    this.edgesGroup = this.svg.append('g').attr('class', 'edges');
    this.pathGroup = this.svg.append('g').attr('class', 'active-path');
    this.nodesGroup = this.svg.append('g').attr('class', 'nodes');

    this.nodes = [];
    this.edges = [];
    this.attackTimeouts = new Map();
    this.activePathEdgeKeys = new Set();
    this.activePathTimeout = null;

    this.NODE_COLORS = {
      ACTIVE: '#22C55E',
      DESTROYED: '#EF4444',
      QUARANTINED: '#EF4444',
      HEALING: '#00BFFF',
      ISOLATED: '#8B5CF6',
      UNVERIFIED: '#FACC15'
    };

    this.simulation = d3
      .forceSimulation([])
      .force('link', d3.forceLink([]).id((d) => d.node_id))
      .force('charge', d3.forceManyBody().strength(-200))
      .force('center', d3.forceCenter(this.width / 2, this.height / 2))
      .force('collide', d3.forceCollide(30))
      .alphaDecay(0.05)
      .velocityDecay(0.4)
      .on('tick', () => this._onTick());

    this.linkSelection = this.edgesGroup.selectAll('line');
    this.nodeSelection = this.nodesGroup.selectAll('circle');
  }

  setData(nodes, edges) {
    this.nodes = Array.isArray(nodes) ? nodes.map((n) => ({ ...n })) : [];
    this.edges = Array.isArray(edges) ? edges.map((e) => ({ ...e })) : [];

    this.simulation.nodes(this.nodes);
    this.simulation.force('link').links(this.edges);

    this.update();

    this.simulation.alpha(1).restart();
  }

  update() {
    this.linkSelection = this.edgesGroup
      .selectAll('line')
      .data(this.edges, (d) => `${d.source?.node_id || d.source}-${d.target?.node_id || d.target}`)
      .join('line')
      .style('stroke', '#14B8A6')
      .attr('stroke-dasharray', '1 8')
      .attr('stroke-opacity', 0.8)
      .attr('stroke-width', (d) => 1 + (Number(d.weight) || 0) * 4);

    this.linkSelection
      .style('stroke', (d) => {
        const key = this._edgeKey(d);
        return this.activePathEdgeKeys.has(key) ? '#FACC15' : '#14B8A6';
      })
      .attr('stroke-opacity', (d) => {
        const key = this._edgeKey(d);
        return this.activePathEdgeKeys.has(key) ? 1.0 : 0.8;
      })
      .attr('stroke-width', (d) => {
        const base = 1 + (Number(d.weight) || 0) * 4;
        const key = this._edgeKey(d);
        return this.activePathEdgeKeys.has(key) ? base + 2.5 : base;
      });

    this.nodeSelection = this.nodesGroup
      .selectAll('circle')
      .data(this.nodes, (d) => d.node_id)
      .join('circle')
      .attr('r', 14)
      .style('fill', (d) => this.NODE_COLORS[d.status] || '#9ca3af')
      .style('stroke', '#111827')
      .attr('stroke-width', 1.2)
      .style('filter', (d) => d.status === 'ACTIVE' ? 'url(#glow)' : 'none');

    this.nodeSelection.selectAll('title').remove();
    this.nodeSelection
      .append('title')
      .text((d) => `${d.node_id} (${d.status || 'UNKNOWN'})`);
  }

  _onTick() {
    this.linkSelection
      .attr('x1', (d) => d.source.x)
      .attr('y1', (d) => d.source.y)
      .attr('x2', (d) => d.target.x)
      .attr('y2', (d) => d.target.y)
      .attr('stroke-width', (d) => {
        const base = 1 + (Number(d.weight) || 0) * 4;
        const key = this._edgeKey(d);
        return this.activePathEdgeKeys.has(key) ? base + 2.5 : base;
      })
      .style('stroke', (d) => {
        const key = this._edgeKey(d);
        return this.activePathEdgeKeys.has(key) ? '#FACC15' : '#14B8A6';
      })
      .attr('stroke-opacity', (d) => {
        const key = this._edgeKey(d);
        return this.activePathEdgeKeys.has(key) ? 1.0 : 0.8;
      });

    this.nodeSelection.attr('cx', (d) => d.x).attr('cy', (d) => d.y);
  }

  _edgeKey(edge) {
    const sourceId = typeof edge.source === 'object' ? edge.source.node_id : edge.source;
    const targetId = typeof edge.target === 'object' ? edge.target.node_id : edge.target;
    return `${sourceId}--${targetId}`;
  }

  highlightPath(pathNodeIds, durationMs = 1500) {
    if (!Array.isArray(pathNodeIds) || pathNodeIds.length < 2) {
      return;
    }

    this.activePathEdgeKeys.clear();
    for (let i = 0; i < pathNodeIds.length - 1; i += 1) {
      const a = pathNodeIds[i];
      const b = pathNodeIds[i + 1];
      this.activePathEdgeKeys.add(`${a}--${b}`);
      this.activePathEdgeKeys.add(`${b}--${a}`);
    }

    this.update();

    if (this.activePathTimeout) {
      clearTimeout(this.activePathTimeout);
    }

    this.activePathTimeout = setTimeout(() => {
      this.activePathEdgeKeys.clear();
      this.update();
      this.activePathTimeout = null;
    }, durationMs);
  }

  showAttack(nodeId, className) {
    const existingTimeout = this.attackTimeouts.get(nodeId);
    if (existingTimeout) {
      clearTimeout(existingTimeout);
    }

    const node = this.nodeSelection.filter((d) => d.node_id === nodeId);
    if (node.empty()) {
      return;
    }

    const attackClass = className || 'attack-highlight';
    node.classed(attackClass, true);
    node.classed('attack-flash', true);

    const timeoutId = setTimeout(() => {
      node.classed(attackClass, false);
      node.classed('attack-flash', false);
      this.attackTimeouts.delete(nodeId);
    }, 1500);

    this.attackTimeouts.set(nodeId, timeoutId);
  }

  showAuthPulse(nodeId) {
    const existingTimeout = this.attackTimeouts.get('auth-' + nodeId);
    if (existingTimeout) {
      clearTimeout(existingTimeout);
    }

    const node = this.nodeSelection.filter((d) => d.node_id === nodeId);
    if (node.empty()) {
      return;
    }

    node.classed('auth-pulse', true);

    const timeoutId = setTimeout(() => {
      node.classed('auth-pulse', false);
      this.attackTimeouts.delete('auth-' + nodeId);
    }, 800);

    this.attackTimeouts.set('auth-' + nodeId, timeoutId);
  }
}

window.MeshVisualizer = MeshVisualizer;
