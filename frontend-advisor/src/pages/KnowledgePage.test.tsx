import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import KnowledgePage from './KnowledgePage'

const listKnowledge = vi.hoisted(() => vi.fn())

vi.mock('../agentApi', () => ({
  listKnowledge,
  createKnowledge: vi.fn(),
  updateKnowledge: vi.fn(),
  deleteKnowledge: vi.fn(),
}))

beforeEach(() => {
  listKnowledge.mockReset()
  listKnowledge.mockResolvedValue({
    items: [
      {
        id: 'k1',
        title: '交易纪律',
        mode: 'always',
        enabled: true,
        description: '个人风控要求',
        body: '单笔风险不超过总资金的 2%。',
      },
    ],
  })
})

it('独立知识库页加载并展示现有条目', async () => {
  render(<KnowledgePage />)

  expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument()
  expect(screen.getByText(/必选知识会注入 Agent 系统提示/)).toBeInTheDocument()
  expect(await screen.findByText('交易纪律')).toBeInTheDocument()
  expect(listKnowledge).toHaveBeenCalledOnce()
})

it('新建表单在描述字段展示写作提示', async () => {
  const user = userEvent.setup()
  render(<KnowledgePage />)
  await screen.findByText('交易纪律')

  await user.click(screen.getByRole('button', { name: '新建条目' }))
  expect(
    screen.getByText(/写清主题与适用场景|写清触发场景与适用问题/),
  ).toBeInTheDocument()

  await user.selectOptions(screen.getByRole('combobox'), 'on_demand')
  expect(screen.getByText(/Agent 靠描述判断何时加载正文/)).toBeInTheDocument()
})
