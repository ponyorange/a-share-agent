import { type ReactNode } from 'react'

export function MobileDisclosure(props: {
  summary: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <details className={['mobile-disclosure', props.className].filter(Boolean).join(' ')}>
      <summary>{props.summary}</summary>
      {props.children}
    </details>
  )
}
