window.DQRMAN = window.DQRMAN || {};

window.DQRMAN.utils = {
  toUTCClock() {
    const now = new Date();
    return now.toISOString().slice(11, 19) + ' UTC';
  },

  formatTimestamp(ts) {
    if (!ts) return '--';
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return '--';
    return d.toISOString().slice(11, 23) + ' UTC';
  },

  formatLatencyMs(value) {
    if (!Number.isFinite(Number(value))) return '--';
    return `${Math.round(Number(value))}ms`;
  },

  formatTrust(value) {
    if (!Number.isFinite(Number(value))) return '0.00';
    return Number(value).toFixed(2);
  },

  truncateKey(hexValue) {
    if (!hexValue || typeof hexValue !== 'string') return '--';
    const clean = hexValue.replace(/[^a-fA-F0-9]/g, '');
    const groups = clean.match(/.{1,4}/g) || [];
    return groups.slice(0, 4).join('-') + (groups.length > 4 ? '...' : '');
  },

  severityFromEvent(eventType) {
    const e = String(eventType || '').toUpperCase();
    if (e.includes('REPLAY') || e.includes('SPOOF') || e.includes('DESTROYED') || e.includes('FAILED')) {
      return 'Alert';
    }
    if (e.includes('QUARANTINED') || e.includes('DEGRADED') || e.includes('ANOMALY') || e.includes('RATE_LIMITED')) {
      return 'Warning';
    }
    return 'Info';
  },

  eventColorClass(eventType) {
    const e = String(eventType || '').toUpperCase();
    if (e.includes('AUTH')) return 'badge-green';
    if (e.includes('REPLAY')) return 'badge-amber';
    if (e.includes('SPOOF')) return 'badge-purple';
    if (e.includes('DESTROYED') || e.includes('FAILED')) return 'badge-red';
    if (e.includes('ROUTE') || e.includes('LOCATION')) return 'badge-blue';
    return 'badge-blue';
  },
};
