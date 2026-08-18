import { createContext, useContext } from 'react'

export const RefreshContext = createContext(null)

export function useRefreshContext() {
  return useContext(RefreshContext)
}
