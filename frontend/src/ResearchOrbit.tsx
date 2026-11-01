import { useState } from 'react'

export function ResearchOrbit({ onTopic }: { onTopic: (topic: string) => void }) {
  const [paused, setPaused] = useState(false)

  return <>
    <div className={`orbit-map${paused ? ' is-paused' : ''}`} aria-label="研究主题导航图">
      <svg viewBox="0 0 300 280" aria-hidden="true">
        <defs><radialGradient id="orbitGlow"><stop stopColor="#4ee1be" stopOpacity=".2"/><stop offset="1" stopColor="#4ee1be" stopOpacity="0"/></radialGradient></defs>
        <circle className="orbit-animated orbit-aura" cx="150" cy="140" r="138" fill="url(#orbitGlow)"/>
        <g fill="none" stroke="currentColor">
          <circle cx="150" cy="140" r="44"/><circle cx="150" cy="140" r="83"/><circle cx="150" cy="140" r="123"/>
          <g className="orbit-animated orbit-ring">
            <ellipse cx="150" cy="140" rx="123" ry="48"/>
            <ellipse className="orbit-track-accent" cx="150" cy="140" rx="123" ry="48" pathLength="100" strokeDasharray="18 32"/>
          </g>
          <g className="orbit-animated orbit-ring-counter">
            <ellipse cx="150" cy="140" rx="103" ry="57"/>
            <ellipse className="orbit-track-secondary" cx="150" cy="140" rx="103" ry="57" pathLength="100" strokeDasharray="8 42"/>
          </g>
          <g className="orbit-animated orbit-ticks"><circle cx="150" cy="140" r="112" pathLength="120" strokeDasharray=".15 4.85"/></g>
          <path d="m75 74 75 66 77-43m-77 43 40 84m-40-84-95 17m95-17-64 80M27 140h246M150 17v246"/>
        </g>
        <g className="orbit-animated orbit-particle orbit-particle-outer">
          <g fill="none" stroke="#78e5c3" strokeLinecap="round">
            <path d="M43.5 78.5A123 123 0 0 1 150 17" strokeWidth="2" opacity=".12"/>
            <path d="M70.9 45.8A123 123 0 0 1 150 17" strokeWidth="2.5" opacity=".24"/>
            <path d="M107.9 24.4A123 123 0 0 1 150 17" strokeWidth="3" opacity=".55"/>
          </g>
          <circle cx="150" cy="17" r="10" fill="#78e5c3" opacity=".1"/>
          <circle cx="150" cy="17" r="6" fill="#78e5c3" opacity=".3"/>
          <circle cx="150" cy="17" r="3.5" fill="#e1fff4"/>
        </g>
        <g className="orbit-animated orbit-particle orbit-particle-inner">
          <g fill="none" stroke="#9adced" strokeLinecap="round">
            <path d="M191.5 211.9A83 83 0 0 0 233 140" strokeWidth="2" opacity=".15"/>
            <path d="M213.6 193.4A83 83 0 0 0 233 140" strokeWidth="2.5" opacity=".3"/>
            <path d="M228 168.4A83 83 0 0 0 233 140" strokeWidth="3" opacity=".6"/>
          </g>
          <circle cx="233" cy="140" r="9" fill="#9adced" opacity=".12"/>
          <circle cx="233" cy="140" r="5.5" fill="#9adced" opacity=".3"/>
          <circle cx="233" cy="140" r="3" fill="#e3faff"/>
        </g>
        <g fill="#78e5c3"><circle cx="75" cy="74" r="4"/><circle cx="227" cy="97" r="4"/><circle cx="190" cy="224" r="4"/><circle cx="55" cy="157" r="4"/><circle cx="86" cy="220" r="4"/></g>
        <g className="orbit-animated orbit-core"><circle cx="150" cy="140" r="7" fill="#78e5c3"/><path d="m150 113 9 18 20 9-20 9-9 18-9-18-20-9 20-9Z" stroke="#bceacb" fill="#17372c"/></g>
        <text x="22" y="62">MULTI-AGENT</text><text x="225" y="85">EVAL</text><text x="186" y="243">REASONING</text>
        <text x="18" y="142">AGENT RL</text><text x="12" y="252">POST-TRAINING</text>
      </svg>
      <button className="orbit-hit hit-agents" title="检索多智能体协作" aria-label="检索多智能体协作" onClick={() => onTopic('多智能体协作')}/>
      <button className="orbit-hit hit-eval" title="检索 Agent 评测" aria-label="检索 Agent 评测" onClick={() => onTopic('Agent 评测')}/>
      <button className="orbit-hit hit-reason" title="检索推理" aria-label="检索推理" onClick={() => onTopic('reasoning')}/>
      <button className="orbit-hit hit-rl" title="检索 Agent 强化学习" aria-label="检索 Agent 强化学习" onClick={() => onTopic('Agent 强化学习')}/>
      <button className="orbit-hit hit-post-training" title="检索大模型后训练" aria-label="检索大模型后训练" onClick={() => onTopic('大模型后训练')}/>
    </div>
    <button className="orbit-motion-toggle" aria-pressed={paused} onClick={() => setPaused(value => !value)}>{paused ? '播放动效' : '暂停动效'}</button>
  </>
}
