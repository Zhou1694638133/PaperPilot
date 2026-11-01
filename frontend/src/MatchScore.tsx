import type { Recommendation } from './types'

const formatScore = (value: number) => Number(value.toFixed(1)).toString()
const featureDescriptions: Record<string, { label: string; description: string }> = {
  '研究兴趣命中比例': { label: '研究兴趣覆盖', description: '标题或摘要命中的研究兴趣，占本次评分所用全部研究兴趣的比例。' },
  '命中词的标题覆盖率': { label: '标题关键词覆盖', description: '已在标题或摘要中命中的去重关键词，有多少也出现在标题中。' },
}

export function MatchScore({ recommendation }: { recommendation: Recommendation }) {
  const { score, features, method, coverage } = recommendation
  const dimensions = features.map(feature => {
    const weight = Number.isFinite(feature.weight) && feature.weight >= 0
      ? Math.min(1, feature.weight <= 1 ? feature.weight : feature.weight / 100) : null
    const value = feature.value !== null && Number.isFinite(feature.value)
      ? Math.min(100, Math.max(0, feature.value)) : null
    return { ...feature, weight, value, contribution: value !== null && weight !== null ? value * weight : null }
  })
  const scope = coverage === 'abstract' ? '标题与摘要' : coverage === 'fulltext' ? '全文' : '范围待确认'

  return <section className="match-score" aria-label="研究兴趣匹配分说明">
    <div className="match-score-heading">
      <div><h3>关键词匹配对照</h3><span>{method === 'rules-v1' ? '关键词规则评分' : method || '评分方法未知'}</span></div>
      <strong className="match-score-total">{Number.isFinite(score) ? formatScore(score) : '—'}<small> / 100</small></strong>
    </div>
    <p className="match-score-scope">本次评分依据：{scope}</p>
    <div className="match-score-track" aria-hidden="true">
      {dimensions.map((dimension, index) => <span key={`${dimension.name}-${index}`} className={`match-tone-${index % 2}`} style={{ width: `${dimension.contribution ?? 0}%` }}/>) }
    </div>
    <p className="match-score-note">用于相关性排序，不代表论文质量或结论正确概率。</p>
    <div className="match-dimensions">
      {dimensions.length ? dimensions.map((dimension, index) => <div className={`match-dimension match-tone-${index % 2}`} key={`${dimension.name}-${index}`}>
        <div className="match-dimension-heading"><span><i aria-hidden="true"/>{featureDescriptions[dimension.name]?.label || dimension.name}</span><strong>{dimension.contribution === null ? '未知' : `+${formatScore(dimension.contribution)}`}<small> 分</small></strong></div>
        <div className="match-dimension-meta"><span>命中比例 {dimension.value === null ? '未知' : `${formatScore(dimension.value)}%`}</span><span>权重 {dimension.weight === null ? '未知' : `${formatScore(dimension.weight * 100)}%`}</span></div>
      </div>) : <p className="match-score-note">暂无评分明细，无法拆解分数来源。</p>}
    </div>
    <details className="match-score-details">
      <summary>分数如何计算</summary>
      {dimensions.map((dimension, index) => <div key={`${dimension.name}-${index}`}>
        <p><b>{featureDescriptions[dimension.name]?.label || dimension.name}</b>：{featureDescriptions[dimension.name]?.description || '按后端返回的特征值与权重展示。'}</p>
        <p className="match-formula">{dimension.value === null || dimension.weight === null || dimension.contribution === null ? '数据不完整，本项贡献未知。' : `${formatScore(dimension.value)} × ${formatScore(dimension.weight * 100)}% = ${formatScore(dimension.contribution)} 分`}</p>
      </div>)}
      <p>总分以服务端结果为准；明细四舍五入后可能存在微小差异。未知项不视为 0 分。</p>
      {method === 'rules-v1' && <p>当前评分未使用语义模型或大模型重排；关键词未命中不等于论文不相关。</p>}
    </details>
  </section>
}
