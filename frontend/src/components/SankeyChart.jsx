import { useEffect, useRef } from 'react'
import * as d3 from 'd3'
import { sankey, sankeyLinkHorizontal } from 'd3-sankey'

/**
 * Sankey du compte de résultats
 * Noeuds : CA → Marge brute → Résultat net
 */
export default function SankeyChart({ years, selectedYear }) {
  const svgRef = useRef(null)

  const yearData = years?.find(y => y.year === selectedYear) || years?.[years.length - 1]

  useEffect(() => {
    if (!yearData || !svgRef.current) return

    const ca          = yearData.chiffre_affaires   || 0
    const margeBrute  = yearData.ratios?.marge_brute || 0
    const resultatNet = yearData.resultat_net        || 0

    // Sankey nécessite des valeurs positives
    if (ca <= 0) return

    const width  = 600
    const height = 300
    const margin = { top: 20, right: 120, bottom: 20, left: 120 }

    const svg = d3.select(svgRef.current)
    svg.selectAll('*').remove()
    svg.attr('viewBox', `0 0 ${width} ${height}`)

    const nodes = [
      { name: "Chiffre d'affaires" },
      { name: 'Marge brute' },
      { name: 'Résultat net' },
    ]

    const links = []
    if (ca > 0 && margeBrute > 0)  links.push({ source: 0, target: 1, value: Math.abs(margeBrute) })
    if (margeBrute > 0 && Math.abs(resultatNet) > 0)
      links.push({ source: 1, target: 2, value: Math.abs(resultatNet) })

    if (links.length === 0) return

    const sankeyGen = sankey()
      .nodeWidth(20)
      .nodePadding(30)
      .extent([[margin.left, margin.top], [width - margin.right, height - margin.bottom]])

    const { nodes: sNodes, links: sLinks } = sankeyGen({ nodes: nodes.map(d => ({...d})), links })

    const color = d3.scaleOrdinal(d3.schemeTableau10)

    const g = svg.append('g')

    // Links
    g.append('g').attr('fill', 'none').attr('stroke-opacity', 0.4)
      .selectAll('path')
      .data(sLinks)
      .join('path')
        .attr('d', sankeyLinkHorizontal())
        .attr('stroke', d => color(d.source.index))
        .attr('stroke-width', d => Math.max(1, d.width))

    // Nodes
    g.append('g')
      .selectAll('rect')
      .data(sNodes)
      .join('rect')
        .attr('x', d => d.x0)
        .attr('y', d => d.y0)
        .attr('height', d => Math.max(1, d.y1 - d.y0))
        .attr('width', d => d.x1 - d.x0)
        .attr('fill', d => color(d.index))

    // Labels
    g.append('g').style('font', '12px sans-serif')
      .selectAll('text')
      .data(sNodes)
      .join('text')
        .attr('x', d => d.x0 < width / 2 ? d.x1 + 6 : d.x0 - 6)
        .attr('y', d => (d.y1 + d.y0) / 2)
        .attr('dy', '0.35em')
        .attr('text-anchor', d => d.x0 < width / 2 ? 'start' : 'end')
        .text(d => `${d.name}: ${(d.value / 1000).toFixed(0)}k€`)

  }, [yearData])

  if (!yearData) return <p style={{ color: '#888' }}>Aucune donnée financière disponible</p>

  return <svg ref={svgRef} style={{ width: '100%', maxWidth: 600, height: 300 }} />
}
