import type { Metadata } from 'next'
import { Geist, Geist_Mono } from 'next/font/google'
import { AgentProvider } from '@/contexts/agent-context'
import { RobotConnectionProvider } from '@/contexts/robot-connection'
import { UIModeProvider } from '@/contexts/ui-mode-context'
import { WorkflowProvider } from '@/contexts/workflow-context'
import { NavStatusProvider } from '@/contexts/nav-status'

import './globals.css'

const _geist = Geist({ subsets: ['latin'] })
const _geistMono = Geist_Mono({ subsets: ['latin'] })

export const metadata: Metadata = {
  title: 'Robot Task Dashboard',
  description: 'Monitor and manage robotic arm tasks',
  generator: 'v0.app',
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" className="dark">
      <body className="font-sans antialiased">
        <UIModeProvider>
          <RobotConnectionProvider>
            <NavStatusProvider>
              <WorkflowProvider>
                <AgentProvider>
                  {children}
                </AgentProvider>
              </WorkflowProvider>
            </NavStatusProvider>
          </RobotConnectionProvider>
        </UIModeProvider>
      </body>
    </html>
  )
}
