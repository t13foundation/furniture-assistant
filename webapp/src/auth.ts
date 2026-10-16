import { createContext, useContext } from 'react'
import type { AuthState } from './types'

export const AuthContext = createContext<{ auth: AuthState; logout: () => Promise<void> } | null>(null)

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within App')
  return context
}

export function isViewer(auth: AuthState) {
  return auth.auth_enabled && auth.role === 'viewer'
}
