import { createContext, useContext } from 'react'

export const ModalContext = createContext(null)

export function useModalContext() {
  return useContext(ModalContext)
}
