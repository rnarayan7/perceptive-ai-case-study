"use client";

// CommentableSection wraps one analysis region and lets an analyst flag whether it is
// accurate by chatting briefly with a feedback-intake agent. It renders its children
// unchanged, pins a comment trigger to the bottom-right of the region, and shows a
// floating "Comment" button when the analyst highlights text inside the region. Both open
// the same compact chat popover; a highlight pre-loads the quote being questioned.
//
// Prop contract (reuse this shape verbatim for every commentable region):
//   company:      string  - ticker the section belongs to (e.g. "KYMR")
//   sectionId:    string  - stable id for the section (e.g. "efficacy", or a memo section id)
//   sectionLabel: string  - human label shown to the agent (e.g. "Efficacy")
//   sectionText:  string  - the analysis text used as context (takeaway + points/prose joined)
//   children:     the section content to render inside the wrapper
//
// The wrapper is position:relative, so it anchors its own trigger and popover; the region
// it wraps does not need to be positioned.

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { FeedbackMessage } from "@/lib/types";

type SelectionButton = { top: number; left: number; quote: string };

export function CommentableSection({
  company,
  sectionId,
  sectionLabel,
  sectionText,
  children,
}: {
  company: string;
  sectionId: string;
  sectionLabel: string;
  sectionText: string;
  children: React.ReactNode;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [quote, setQuote] = useState<string | null>(null);
  const [selBtn, setSelBtn] = useState<SelectionButton | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<FeedbackMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [captured, setCaptured] = useState(false);
  const [error, setError] = useState(false);

  // Show the floating Comment button when the analyst highlights text inside this region.
  const onMouseUp = useCallback(() => {
    const root = rootRef.current;
    if (!root) return;
    const sel = window.getSelection();
    const text = sel?.toString().trim() ?? "";
    if (!sel || sel.rangeCount === 0 || !text) {
      setSelBtn(null);
      return;
    }
    // Only react to selections that live inside this region.
    if (!root.contains(sel.anchorNode) || !root.contains(sel.focusNode)) {
      setSelBtn(null);
      return;
    }
    const rangeRect = sel.getRangeAt(0).getBoundingClientRect();
    const rootRect = root.getBoundingClientRect();
    // Place the button just to the right of the highlighted text, vertically centered on
    // it; clamp so it stays inside the region when the selection reaches the right edge.
    setSelBtn({
      top: rangeRect.top - rootRect.top + rangeRect.height / 2 - 12,
      left: Math.min(rangeRect.right - rootRect.left + 6, rootRect.width - 80),
      quote: text,
    });
  }, []);

  // Dismiss the floating button on an outside click when the popover is closed.
  useEffect(() => {
    if (!selBtn) return;
    const onDocDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setSelBtn(null);
    };
    document.addEventListener("mousedown", onDocDown);
    return () => document.removeEventListener("mousedown", onDocDown);
  }, [selBtn]);

  function openChat(withQuote: string | null) {
    setQuote(withQuote);
    setOpen(true);
    setSelBtn(null);
  }

  function closeChat() {
    setOpen(false);
    setSelBtn(null);
  }

  async function send() {
    const text = input.trim();
    if (!text || sending) return;
    const next: FeedbackMessage[] = [...messages, { role: "analyst", text }];
    setMessages(next);
    setInput("");
    setSending(true);
    setError(false);
    try {
      const res = await api.feedbackChat({
        company,
        section_id: sectionId,
        section_label: sectionLabel,
        section_text: sectionText,
        highlighted_quote: quote,
        session_id: sessionId,
        messages: next,
      });
      setSessionId(res.session_id);
      setMessages([...next, { role: "agent", text: res.reply }]);
      setCaptured(res.captured);
    } catch {
      setError(true);
      setMessages(next);
    } finally {
      setSending(false);
    }
  }

  return (
    <div ref={rootRef} className="relative h-full" onMouseUp={onMouseUp}>
      {children}

      {/* Floating button shown near a text selection. */}
      {selBtn && !open && (
        <button
          type="button"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => openChat(selBtn.quote)}
          style={{ top: selBtn.top, left: selBtn.left }}
          className="absolute z-30 rounded-md border border-border bg-surface px-2 py-1 text-[11px] font-semibold text-accent-text shadow-md hover:bg-accent-soft2"
        >
          Comment
        </button>
      )}

      {/* Bottom-right trigger, always available on the region. */}
      {!open && (
        <button
          type="button"
          onClick={() => openChat(null)}
          title="Flag this section"
          className="absolute bottom-2 right-2 z-20 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-surface text-tertiary shadow-sm hover:border-border-strong hover:text-accent-text"
        >
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path
              d="M2 3.5A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5v6A1.5 1.5 0 0 1 12.5 11H6l-3 2.5V11H3.5A1.5 1.5 0 0 1 2 9.5v-6Z"
              stroke="currentColor"
              strokeWidth="1.3"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      )}

      {open && (
        <ChatPopover
          sectionLabel={sectionLabel}
          quote={quote}
          messages={messages}
          input={input}
          setInput={setInput}
          send={send}
          sending={sending}
          captured={captured}
          error={error}
          onClose={closeChat}
        />
      )}
    </div>
  );
}

function ChatPopover({
  sectionLabel,
  quote,
  messages,
  input,
  setInput,
  send,
  sending,
  captured,
  error,
  onClose,
}: {
  sectionLabel: string;
  quote: string | null;
  messages: FeedbackMessage[];
  input: string;
  setInput: (v: string) => void;
  send: () => void;
  sending: boolean;
  captured: boolean;
  error: boolean;
  onClose: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, sending]);

  return (
    <div className="absolute bottom-2 right-2 z-40 flex w-[300px] flex-col rounded-lg border border-border bg-surface shadow-xl">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="min-w-0">
          <div className="text-[12px] font-semibold text-ink">Flag: {sectionLabel}</div>
          <div className="text-[10px] text-tertiary">Is this section accurate?</div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="text-tertiary hover:text-ink"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      {quote && (
        <div className="mx-3 mt-2 border-l-2 border-accent-soft pl-2 text-[11px] italic leading-4 text-secondary">
          &ldquo;{quote.length > 140 ? `${quote.slice(0, 140)}…` : quote}&rdquo;
        </div>
      )}

      <div ref={scrollRef} className="max-h-[220px] min-h-[64px] overflow-y-auto px-3 py-2">
        {messages.length === 0 && (
          <p className="text-[11px] leading-4 text-tertiary">
            Describe what looks off and it will be logged for review.
          </p>
        )}
        <div className="space-y-2">
          {messages.map((m, i) => (
            <div
              key={i}
              className={
                m.role === "analyst"
                  ? "ml-6 rounded-md bg-accent-soft2 px-2 py-1.5 text-[12px] leading-4 text-ink"
                  : "mr-6 rounded-md bg-sunken px-2 py-1.5 text-[12px] leading-4 text-secondary"
              }
            >
              {m.text}
            </div>
          ))}
          {sending && <div className="mr-6 px-2 text-[11px] text-tertiary">…</div>}
          {error && (
            <div className="mr-6 px-2 text-[11px] text-short">
              Couldn&apos;t send that. Try again.
            </div>
          )}
        </div>
      </div>

      {captured ? (
        <div className="flex items-center gap-1.5 border-t border-border px-3 py-2 text-[11px] font-medium text-conf-high">
          <span className="h-1.5 w-1.5 rounded-full bg-conf-high" />
          Feedback logged
        </div>
      ) : (
        <div className="flex items-end gap-1.5 border-t border-border p-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={1}
            placeholder="What looks inaccurate?"
            className="max-h-24 flex-1 resize-none rounded-md border border-border px-2 py-1.5 text-[12px] leading-4 text-ink outline-none focus:border-accent"
          />
          <button
            type="button"
            onClick={send}
            disabled={sending || !input.trim()}
            className="rounded-md bg-accent px-2.5 py-1.5 text-[12px] font-semibold text-white disabled:opacity-40"
          >
            Send
          </button>
        </div>
      )}
    </div>
  );
}
