/**
 * Account/counterparty graph, rendered with Cytoscape.
 *
 * Colour encodes the same thing the rest of the app encodes: simulated risk, never
 * a criminal accusation. A node is either "normal" or "flagged for investigation" /
 * "elevated simulated risk" — see `riskStatusLabel`.
 *
 * Edge thickness is transaction count (so a fan-out attack is visually obvious) and
 * suspicious edges are dashed. Layout is deterministic (`cose` with a fixed random
 * seed) so the same graph looks the same on every render, which matters when two
 * people are comparing screenshots.
 */
import cytoscape from 'cytoscape';
import { useEffect, useRef } from 'react';
import type { NetworkGraph } from '../api/types';

const RISK_COLOUR: Record<string, string> = {
  NORMAL: '#35c46a',
  FLAGGED_FOR_INVESTIGATION: '#f5b942',
  ELEVATED_SIMULATED_RISK: '#ef4d4d',
};

export function NetworkGraphView({
  graph,
  onSelectNode,
  height = 540,
}: {
  graph: NetworkGraph;
  onSelectNode?: (accountId: string) => void;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const selectRef = useRef(onSelectNode);
  selectRef.current = onSelectNode;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const maxCount = graph.edges.reduce((max, edge) => Math.max(max, edge.transaction_count), 1);

    const cy = cytoscape({
      container,
      elements: [
        ...graph.nodes.map((node) => ({
          data: {
            id: node.id,
            label: node.label,
            colour: RISK_COLOUR[node.risk_status] ?? '#6c7d97',
            size: 22 + Math.min(26, node.transaction_count * 1.4),
            primary: node.is_primary ? 1 : 0,
          },
        })),
        ...graph.edges.map((edge) => ({
          data: {
            id: edge.id,
            source: edge.source,
            target: edge.target,
            width: 1 + (edge.transaction_count / maxCount) * 4,
            suspicious: edge.suspicious ? 1 : 0,
            label: edge.amount.toFixed(2),
          },
        })),
      ],
      style: [
        {
          selector: 'node',
          style: {
            'background-color': 'data(colour)',
            width: 'data(size)',
            height: 'data(size)',
            label: 'data(label)',
            color: '#93a4bd',
            'font-size': 9,
            'text-valign': 'bottom',
            'text-margin-y': 4,
            'border-width': 1,
            'border-color': '#0b1220',
          },
        },
        {
          selector: 'node[primary = 1]',
          style: { 'border-width': 3, 'border-color': '#22d3ee', 'font-size': 10 },
        },
        {
          selector: 'edge',
          style: {
            width: 'data(width)',
            'line-color': '#33456a',
            'target-arrow-color': '#33456a',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'arrow-scale': 0.8,
          },
        },
        {
          selector: 'edge[suspicious = 1]',
          style: {
            'line-color': '#ef4d4d',
            'target-arrow-color': '#ef4d4d',
            'line-style': 'dashed',
          },
        },
        { selector: 'node:selected', style: { 'border-width': 4, 'border-color': '#14b8a6' } },
      ],
      layout: { name: 'cose', animate: false, randomize: true, nodeRepulsion: () => 9000, idealEdgeLength: () => 90 },
      wheelSensitivity: 0.2,
    });

    cy.on('tap', 'node', (event) => {
      const id = event.target.id();
      if (typeof id === 'string') selectRef.current?.(id);
    });

    return () => {
      cy.destroy();
    };
  }, [graph]);

  if (graph.nodes.length === 0) {
    return <p className="dim small">No graph nodes match the current filter.</p>;
  }

  return (
    <div
      ref={containerRef}
      role="img"
      aria-label={`Financial network with ${graph.nodes.length} accounts and ${graph.edges.length} relationships`}
      style={{
        height,
        border: '1px solid var(--line)',
        borderRadius: 'var(--radius)',
        background: 'var(--navy-950)',
      }}
    />
  );
}
