import { useEffect, useState } from 'react'
import { getSources } from '../api'
import { FALLBACK_SOURCES, type DataSourceInfo } from '../sources'

export function useSources() {
  const [sources, setSources] = useState<DataSourceInfo[]>(FALLBACK_SOURCES)

  useEffect(() => {
    getSources()
      .then((data) => {
        if (data.sources?.length) {
          setSources(
            data.sources.map((s) => ({
              id: s.id,
              label: s.label,
              features: s.features as DataSourceInfo['features'],
              docs_url: s.docs_url,
              ready: s.ready,
              message: s.message,
            })),
          )
        }
      })
      .catch(() => {
        /* keep fallback */
      })
  }, [])

  return sources
}
