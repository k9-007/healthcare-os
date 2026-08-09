import { createBrowserRouter, Navigate } from 'react-router-dom'
import { AppShell } from '@/components/layout/AppShell'
import { Dashboard } from '@/pages/Dashboard'
import { Patients } from '@/pages/Patients'
import { PatientDetail } from '@/pages/PatientDetail'
import { Brain } from '@/pages/Brain'
import { Documents } from '@/pages/Documents'
import { Settings } from '@/pages/Settings'

export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { path: '/', element: <Navigate to="/dashboard" replace /> },
      { path: '/dashboard', element: <Dashboard /> },
      { path: '/patients', element: <Patients /> },
      { path: '/patients/:id', element: <PatientDetail /> },
      { path: '/brain', element: <Brain /> },
      { path: '/documents', element: <Documents /> },
      { path: '/settings', element: <Settings /> },
    ],
  },
])
