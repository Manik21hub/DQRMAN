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
      .attr('preserveAspectRatio', 'xMidYMid meet');

    this.edgesGroup = this.svg.append('g').attr('class', 'edges');
    this.nodesGroup = this.svg.append('g').attr('class', 'nodes');

    this.nodes = [];
    this.edges = [];
    this.attackTimeouts = new Map();

    this.NODE_COLORS = {
      ACTIVE: '#22c55e',
      DESTROYED: '#ef4444',
      QUARANTINED: '#f97316',
      HEALING: '#3b82f6',
      ISOLATED: '#a855f7'
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
      .style('stroke', '#94a3b8')
      .attr('stroke-opacity', 0.8)
      .attr('stroke-width', (d) => 1 + (Number(d.weight) || 0) * 4);

    this.nodeSelection = this.nodesGroup
      .selectAll('circle')
      .data(this.nodes, (d) => d.node_id)
      .join('circle')
      .attr('r', 14)
      .style('fill', (d) => this.NODE_COLORS[d.status] || '#9ca3af')
      .style('stroke', '#111827')
      .attr('stroke-width', 1.2);

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
      .attr('stroke-width', (d) => 1 + (Number(d.weight) || 0) * 4);

    this.nodeSelection.attr('cx', (d) => d.x).attr('cy', (d) => d.y);
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
