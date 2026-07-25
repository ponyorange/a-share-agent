import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import KnowledgePage from './KnowledgePage'

const listKnowledge = vi.hoisted(() => vi.fn())
const fetchAgentSystemPrompt = vi.hoisted(() => vi.fn())
const saveAgentSystemPrompt = vi.hoisted(() => vi.fn())

vi.mock('../agentApi', () => ({
  listKnowledge,
  createKnowledge: vi.fn(),
  updateKnowledge: vi.fn(),
  deleteKnowledge: vi.fn(),
  fetchAgentSystemPrompt,
  saveAgentSystemPrompt,
}))

beforeEach(() => {
  listKnowledge.mockReset()
  fetchAgentSystemPrompt.mockReset()
  saveAgentSystemPrompt.mockReset()
  fetchAgentSystemPrompt.mockResolvedValue({ system_prompt: '请自称小顾。' })
  saveAgentSystemPrompt.mockResolvedValue({ system_prompt: '请自称小顾。' })
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

it('Agent 配置页展示系统提示词和知识库', async () => {
  render(<KnowledgePage />)

  expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '系统提示词' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '知识库' })).toBeInTheDocument()
  expect(
    screen.getByText(/系统提示词追加在产品规则之后/),
  ).toBeInTheDocument()
  expect(screen.getByText(/必选知识会注入消息上下文/)).toBeInTheDocument()
  expect(await screen.findByDisplayValue('请自称小顾。')).toBeInTheDocument()
  expect(await screen.findByText('交易纪律')).toBeInTheDocument()
  expect(listKnowledge).toHaveBeenCalledOnce()
  expect(fetchAgentSystemPrompt).toHaveBeenCalledOnce()
})

it('可保存用户系统提示词并展示字数', async () => {
  const user = userEvent.setup()
  render(<KnowledgePage />)

  const input = await screen.findByLabelText('系统提示词（≤ 6000 字）')
  await user.clear(input)
  await user.type(input, '请保持简洁')
  expect(screen.getByText('5/6000')).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '保存系统提示词' }))
  expect(saveAgentSystemPrompt).toHaveBeenCalledWith('请保持简洁')
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
