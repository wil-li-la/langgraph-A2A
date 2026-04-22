"use client"

import { useState, useRef, useEffect, useCallback } from "react"

interface VoiceInputProps {
  onResult: (text: string) => void
  prompt?: string | null
  awaiting?: boolean
}

export function VoiceInput({ onResult, prompt, awaiting = false }: VoiceInputProps) {
  const [text, setText] = useState("")
  const [isListening, setIsListening] = useState(false)
  const [speechSupported, setSpeechSupported] = useState(false)
  const recognitionRef = useRef<SpeechRecognition | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SR) return
    try {
      const test = new SR()
      test.abort?.()
      setSpeechSupported(true)
    } catch {
      setSpeechSupported(false)
    }
  }, [])

  const startListening = useCallback(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SR) return
    if (recognitionRef.current) return  // already listening

    const recognition = new SR()
    recognition.lang = "zh-TW"
    recognition.continuous = true
    recognition.interimResults = false

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let transcript = ""
      for (let i = 0; i < event.results.length; i++) {
        transcript += event.results[i]?.[0]?.transcript ?? ""
      }
      setText(transcript)
    }

    recognition.onerror = () => {
      setIsListening(false)
      recognitionRef.current = null
    }

    recognition.onend = () => {
      setIsListening(false)
      recognitionRef.current = null
    }

    recognitionRef.current = recognition
    recognition.start()
    setIsListening(true)
  }, [])

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop()
  }, [])

  const handleSubmit = () => {
    const trimmed = text.trim()
    if (!trimmed) return
    onResult(trimmed)
    setText("")
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <h2 className="font-mono text-lg font-medium tracking-wide text-foreground">
          VOICE INPUT
        </h2>
        {awaiting && (
          <span className="rounded-full border border-sky-500/50 bg-sky-500/10 px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-wide text-sky-400">
            Awaiting
          </span>
        )}
        {isListening && (
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400/50" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-red-400" />
          </span>
        )}
      </div>

      {prompt && (
        <div className="rounded-md border border-sky-500/30 bg-sky-500/5 px-3 py-2 font-mono text-sm text-foreground/90">
          {prompt}
        </div>
      )}

      {speechSupported ? (
        <button
          onPointerDown={(e) => {
            e.preventDefault()
            startListening()
          }}
          onPointerUp={stopListening}
          onPointerLeave={() => {
            if (isListening) stopListening()
          }}
          onPointerCancel={stopListening}
          onContextMenu={(e) => e.preventDefault()}
          className={`w-full rounded-md border px-3 py-2 font-mono text-sm font-medium transition-colors touch-none ${
            isListening
              ? "border-red-500/50 bg-red-500/10 text-red-500"
              : "border-sky-500/50 bg-sky-500/10 text-sky-400 hover:bg-sky-500/20 active:bg-sky-500/30"
          }`}
        >
          {isListening ? "● REC…" : "HOLD TO TALK"}
        </button>
      ) : (
        <span className="w-full rounded-md border border-border px-3 py-2 text-center font-mono text-sm text-muted-foreground/40">
          No mic
        </span>
      )}

      <div className="flex gap-2">
        <input
          ref={inputRef}
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
          placeholder="或在此輸入 …"
          className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-sky-500/30"
        />

        <button
          onClick={handleSubmit}
          disabled={!text.trim()}
          className="shrink-0 rounded-md border border-border bg-foreground px-4 py-2 font-mono text-sm font-medium text-background transition-opacity hover:opacity-90 disabled:opacity-30"
        >
          SEND
        </button>
      </div>
    </div>
  )
}
