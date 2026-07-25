import { readFileSync } from 'node:fs'
import { expect, it } from 'vitest'

const businessPages = [
  '../src/pages/RecommendationsPage.tsx',
  '../src/pages/AdvicePage.tsx',
  '../src/pages/PortfolioPage.tsx',
  '../src/pages/HistoryPage.tsx',
  '../src/pages/PaperPage.tsx',
  '../src/pages/LeaderboardPage.tsx',
  '../src/pages/PerformancePage.tsx',
  '../src/pages/StrategyPage.tsx',
  '../src/pages/SettingsPage.tsx',
  '../src/pages/KnowledgePage.tsx',
  '../src/pages/AgentSettingsPage.tsx',
  '../src/pages/AgentStrategyPage.tsx',
  '../src/committee/CommitteePage.tsx',
]

function source(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

it.each(businessPages)('%s 不再渲染页面级 h1', (relativePath) => {
  expect(source(relativePath)).not.toMatch(/<h1(?:\s|>)/)
})

it('登录页保留产品一级标题', () => {
  expect(source('../src/pages/LoginPage.tsx')).toContain('<h1>次日顾问</h1>')
})

it('桌面端缩小品牌标题且不改变移动端字号', () => {
  const styles = source('../src/styles.css')
  expect(styles).toMatch(
    /@media \(min-width: 769px\)\s*\{\s*\.brand\s*\{\s*font-size: 1\.5rem;\s*\}\s*\}/,
  )
  expect(styles).toMatch(
    /@media \(max-width: 768px\)[\s\S]*?\.brand\s*\{\s*font-size: 1\.35rem;\s*\}/,
  )
})
