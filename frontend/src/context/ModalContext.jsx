import { createContext, useContext } from 'react'

export const ModalContext = createContext(null)

export function useModalContext() {
  return useContext(ModalContext)
}

// Convenience: access openTelegramSettings from any page without prop drilling
export function useTelegramSettings() {
  const ctx = useContext(ModalContext)
  return ctx?.openTelegramSettings ?? (() => {})
}
