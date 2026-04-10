import type { Metadata } from 'next'
import { Geist, Geist_Mono } from 'next/font/google'
import { RobotConnectionProvider } from '@/contexts/robot-connection'

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
        <RobotConnectionProvider>
          {children}
        </RobotConnectionProvider>
      </body>
    </html>
  )
}
