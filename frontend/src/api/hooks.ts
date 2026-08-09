import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import type { CarePlan, NewPatientInput } from './types'

export const usePatients = () =>
  useQuery({ queryKey: ['patients'], queryFn: api.listPatients })

export const usePatient = (id: number) =>
  useQuery({ queryKey: ['patient', id], queryFn: () => api.getPatient(id) })

export const useCarePlan = (patientId: number) =>
  useQuery({ queryKey: ['care-plan', patientId], queryFn: () => api.getCarePlan(patientId) })

export const useCalls = (patientId?: number) =>
  useQuery({ queryKey: ['calls', patientId ?? 'all'], queryFn: () => api.listCalls(patientId) })

export const useTimeline = (patientId: number) =>
  useQuery({ queryKey: ['timeline', patientId], queryFn: () => api.getTimeline(patientId) })

export const useEscalations = () =>
  useQuery({ queryKey: ['escalations'], queryFn: api.listEscalations })

export const useUpcomingCalls = () =>
  useQuery({ queryKey: ['upcoming-calls'], queryFn: api.listUpcomingCalls })

export const useAnalytics = () =>
  useQuery({ queryKey: ['analytics'], queryFn: api.getAnalytics })

export const useDocuments = () =>
  useQuery({ queryKey: ['documents'], queryFn: api.listDocuments, refetchInterval: 7000 })

export const useBrainHistory = () =>
  useQuery({ queryKey: ['brain-history'], queryFn: api.getBrainHistory })

export function useCreatePatient() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: NewPatientInput) => api.createPatient(input),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['patients'] }),
  })
}

export function useSaveCarePlan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (plan: CarePlan) => api.saveCarePlan(plan),
    onSuccess: (plan) => qc.invalidateQueries({ queryKey: ['care-plan', plan.patientId] }),
  })
}

export function usePlaceCall(patientId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.placeCall(patientId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['calls'] }),
  })
}

export function useSubmitReply() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ callId, audio }: { callId: number; audio?: Blob }) => api.submitReply(callId, audio),
    onSuccess: (call) => {
      qc.invalidateQueries({ queryKey: ['calls'] })
      qc.invalidateQueries({ queryKey: ['timeline', call.patientId] })
      qc.invalidateQueries({ queryKey: ['escalations'] })
      qc.invalidateQueries({ queryKey: ['analytics'] })
      qc.invalidateQueries({ queryKey: ['patient', call.patientId] })
    },
  })
}

export function useDoctorReply(patientId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (message: string) => api.sendDoctorReply(patientId, message),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['calls'] })
      qc.invalidateQueries({ queryKey: ['timeline', patientId] })
      qc.invalidateQueries({ queryKey: ['escalations'] })
    },
  })
}

export function useAckEscalation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.ackEscalation(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['escalations'] }),
  })
}

export function useUploadDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ file, patientId }: { file: File; patientId?: number | null }) =>
      api.uploadDocument(file, patientId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['documents'] }),
  })
}

export interface AskBrainInput {
  question: string
  patientId?: number | null
  patientName?: string
}

export function useAskBrain() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ question, patientId, patientName }: AskBrainInput) =>
      api.askBrain(question, patientId, patientName),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['brain-history'] }),
  })
}
