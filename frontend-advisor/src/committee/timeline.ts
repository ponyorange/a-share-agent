import type { CommitteeEventRecord, ParsedSseEvent } from './committeeApi'

export type TimelineState = {
  events: CommitteeEventRecord[]
  lastEventId?: string
}

export const MAX_TIMELINE_EVENTS = 1000
export const initialTimelineState: TimelineState = { events: [] }

export type TimelineAction =
  | { type: 'reset' }
  | { type: 'hydrate'; events: CommitteeEventRecord[] }
  | { type: 'merge'; events: CommitteeEventRecord[] }
  | { type: 'event'; event: CommitteeEventRecord }

function compareEvents(left: CommitteeEventRecord, right: CommitteeEventRecord) {
  return left.sequence - right.sequence || left.event_id.localeCompare(right.event_id)
}

export function latestEventId(events: CommitteeEventRecord[]) {
  let latest: CommitteeEventRecord | undefined
  for (const event of events) {
    if (!latest || compareEvents(latest, event) < 0) latest = event
  }
  return latest?.event_id
}

function hydrateEvents(events: CommitteeEventRecord[]) {
  const unique = new Map<string, CommitteeEventRecord>()
  for (const event of events) unique.set(event.event_id, event)
  return [...unique.values()]
    .sort(compareEvents)
    .slice(-MAX_TIMELINE_EVENTS)
}

function insertEvent(
  events: CommitteeEventRecord[],
  incoming: CommitteeEventRecord,
) {
  const existingIndex = events.findIndex(
    (event) => event.event_id === incoming.event_id,
  )
  const next = existingIndex < 0
    ? [...events]
    : [...events.slice(0, existingIndex), ...events.slice(existingIndex + 1)]
  let low = 0
  let high = next.length
  while (low < high) {
    const middle = (low + high) >>> 1
    if (compareEvents(next[middle], incoming) <= 0) low = middle + 1
    else high = middle
  }
  next.splice(low, 0, incoming)
  return next.length > MAX_TIMELINE_EVENTS
    ? next.slice(next.length - MAX_TIMELINE_EVENTS)
    : next
}

export function timelineReducer(
  state: TimelineState,
  action: TimelineAction,
): TimelineState {
  if (action.type === 'reset') return initialTimelineState
  let events: CommitteeEventRecord[]
  if (action.type === 'hydrate') events = hydrateEvents(action.events)
  else if (action.type === 'merge') events = hydrateEvents([...state.events, ...action.events])
  else events = insertEvent(state.events, action.event)
  const lastEventId = events.at(-1)?.event_id
  return { events, lastEventId }
}

export function parsedEventToRecord(event: ParsedSseEvent): CommitteeEventRecord {
  const sequence = Number(event.id?.split('-', 1)[0])
  return {
    event_id: event.id ?? `live-${crypto.randomUUID()}`,
    sequence: Number.isFinite(sequence) ? sequence : Date.now(),
    event_type: event.event,
    payload: event.data,
  }
}
