import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyText } from './copyText'

function stubExecCommand(result: boolean) {
  const exec = vi.fn().mockReturnValue(result)
  Object.defineProperty(document, 'execCommand', {
    configurable: true,
    writable: true,
    value: exec,
  })
  return exec
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('copyText', () => {
  it('优先使用 clipboard.writeText', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    await copyText('hello')
    expect(writeText).toHaveBeenCalledWith('hello')
  })

  it('clipboard 失败时回退 execCommand', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    const exec = stubExecCommand(true)

    await copyText('fallback')
    expect(writeText).toHaveBeenCalled()
    expect(exec).toHaveBeenCalledWith('copy')
  })

  it('无 clipboard API 时走 execCommand', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: undefined,
    })
    const exec = stubExecCommand(true)

    await copyText('legacy')
    expect(exec).toHaveBeenCalledWith('copy')
  })

  it('两种方式都失败则抛错', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    })
    stubExecCommand(false)

    await expect(copyText('x')).rejects.toThrow()
  })
})
