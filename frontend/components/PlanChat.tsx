"use client";

// AA-1041 slice 2 — the planning chat panel of the planner (desktop: a tab of the trip panel; phone: its own view).
// It talks to POST /api/trip/chat (lib/api.ts sendChat): the server answers only from Adventure Asia's catalog and may
// attach a proposal built from catalog stops; "Apply to my trip" runs the planner's own mutations (useTrip
// applyChatProposal). A paused chat keeps a calm line; only a money cap closes the composer (AI off or a provider
// outage leaves it open to try again); the rest of the planner is untouched. Kit only: k-* tokens, ui/Button, ui/Badge, ui/AiNotice, ui/States.

import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useTrip } from "@/lib/useTrip";
import { sendChat } from "@/lib/api";
import {
  MAX_MESSAGE_CHARS,
  chatCopy,
  historyForRequest,
  locksComposer,
  noticeFor,
  proposalSummary,
  type ApplyState,
  type ChatMessage,
  type ChatProposal,
} from "@/lib/planChat";
import { noticeLang } from "@/lib/aiNotices";
import { TERMS_AI_PATH } from "@/lib/legal";
import { AiNotice } from "@/ui/AiNotice";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { Icon } from "@/ui/Icon";
import { LoadingState } from "@/ui/States";

type Line = ChatMessage & { notice?: boolean };

let seq = 0;
const nextId = () => `m${++seq}`;

function ProposalCard({
  proposal,
  state,
  onApply,
  lang,
}: {
  proposal: ChatProposal;
  state: ApplyState;
  onApply: () => void;
  lang: "en" | "vi";
}) {
  const copy = chatCopy(lang);
  return (
    <div data-testid="chat-proposal" className="mt-2 rounded-tile border border-k-accent-border bg-k-card p-3">
      <p className="text-xs font-semibold text-k-ink">{proposalSummary(proposal, lang)}</p>
      {proposal.stops.length > 0 && (
        <ol className="mt-2 space-y-1">
          {proposal.stops.map((s, i) => (
            <li key={s.component_id} className="flex items-start gap-2 text-xs text-k-body">
              <span className="mt-px w-4 shrink-0 text-right font-semibold text-k-muted">{i + 1}</span>
              <span className="min-w-0 flex-1">
                <span className="font-semibold text-k-ink">{s.name}</span>
                {s.place && <span className="text-k-muted"> · {s.place}</span>}
              </span>
              {s.new && <Badge tone="accent">New</Badge>}
            </li>
          ))}
        </ol>
      )}
      {proposal.removed.length > 0 && (
        <ul className="mt-2 space-y-1" aria-label="Removed stops">
          {proposal.removed.map((s) => (
            <li key={s.component_id} className="flex items-center gap-2 text-xs text-k-muted">
              <Icon name="minus" className="h-3.5 w-3.5 shrink-0" />
              <span className="line-through">{s.name}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3">
        {state === "applied" ? (
          <p role="status" className="flex items-center gap-1.5 text-xs font-semibold text-k-success">
            <Icon name="check" className="h-4 w-4" /> {copy.applied}
          </p>
        ) : (
          <Button size="sm" onClick={onApply} disabled={state === "applying"} data-testid="chat-apply">
            {state === "applying" ? copy.applying : copy.apply}
          </Button>
        )}
        {state === "failed" && (
          <p role="alert" className="mt-2 text-xs text-k-danger">{copy.applyFailed}</p>
        )}
      </div>
    </div>
  );
}

export default function PlanChat() {
  const { tripId, sessionId, applyChatProposal } = useTrip();
  // The page language (`<html lang>`), English on the server, like the planner's other notices (lib/aiNotices.ts).
  const lang = typeof document !== "undefined" ? noticeLang(document.documentElement.lang) : "en";
  const [lines, setLines] = useState<Line[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [paused, setPaused] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const inputId = useId();
  const copy = chatCopy(lang);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines, sending]);

  const tooLong = draft.trim().length > MAX_MESSAGE_CHARS;

  async function ask(text: string) {
    const message = text.trim();
    if (!message || sending || paused || message.length > MAX_MESSAGE_CHARS) return;
    const history = historyForRequest(lines.filter((l) => !l.notice));
    setLines((ls) => [...ls, { id: nextId(), role: "user", text: message }]);
    setDraft("");
    setSending(true);
    try {
      const answer = await sendChat(tripId, sessionId, message, history);
      if (answer.kind === "reply") {
        setLines((ls) => [...ls, {
          id: nextId(), role: "assistant", text: answer.reply, proposal: answer.proposal,
          aiGenerated: answer.aiGenerated, apply: "idle",
        }]);
      } else {
        if (locksComposer(answer)) setPaused(true);
        setLines((ls) => [...ls, { id: nextId(), role: "assistant", text: noticeFor(answer, copy), notice: true }]);
      }
    } finally {
      setSending(false);
    }
  }

  async function apply(id: string, proposal: ChatProposal) {
    const set = (apply: ApplyState) => setLines((ls) => ls.map((l) => (l.id === id ? { ...l, apply } : l)));
    set("applying");
    set((await applyChatProposal(proposal)) ? "applied" : "failed");
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void ask(draft);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void ask(draft);
    }
  }

  return (
    <section aria-label={copy.title} data-testid="plan-chat" className="flex h-full min-h-0 flex-col bg-k-bg">
      <header className="border-b border-k-line px-4 pb-3 pt-4">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-k-ink">
          <Icon name="chat" className="h-4 w-4 text-k-accent-deep" />
          {copy.title}
        </h2>
        <AiNotice size="sm" variant="narration" termsHref={TERMS_AI_PATH} className="mt-2" testId="chat-ai-notice" />
      </header>

      <div ref={logRef} role="log" aria-live="polite" aria-relevant="additions" className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {lines.length === 0 && (
          <div className="rounded-card border border-dashed border-k-accent-border bg-k-card p-4">
            <p className="text-xs text-k-body">{copy.intro}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {copy.examples.map((ex) => (
                <button
                  key={ex}
                  type="button"
                  onClick={() => setDraft(ex)}
                  className="aa-focus rounded-full border border-k-line bg-k-bg px-3 py-1.5 text-xs text-k-ink transition hover:border-k-accent-border hover:bg-k-accent-tint"
                >
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}
        <ul className="space-y-3">
          {lines.map((l) => (
            <li key={l.id} className={l.role === "user" ? "flex justify-end" : ""} data-testid={`chat-${l.role}`}>
              {l.role === "user" ? (
                <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-card bg-k-accent-tint px-3 py-2 text-sm text-k-ink">
                  <span className="sr-only">{copy.you}: </span>
                  {l.text}
                </p>
              ) : l.notice ? (
                <p role="status" className="flex items-start gap-2 rounded-card border border-k-line bg-k-card px-3 py-2 text-xs text-k-muted">
                  <Icon name="info" className="mt-px h-4 w-4 shrink-0" />
                  <span>{l.text}</span>
                </p>
              ) : (
                <div className="max-w-[95%]">
                  <p className="whitespace-pre-wrap break-words rounded-card border border-k-line bg-k-card px-3 py-2 text-sm text-k-ink"
                    {...(l.aiGenerated ? { "data-ai-generated": "true" } : {})}>
                    <span className="sr-only">{copy.assistant}: </span>
                    {l.text}
                  </p>
                  {l.proposal && (
                    <ProposalCard proposal={l.proposal} state={l.apply ?? "idle"} lang={lang}
                      onApply={() => void apply(l.id, l.proposal as ChatProposal)} />
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
        {sending && (
          <div className="mt-3">
            <LoadingState label={copy.sending} rows={1} compact />
          </div>
        )}
      </div>

      <form onSubmit={onSubmit} className="border-t border-k-line bg-k-card px-4 py-3">
        <label htmlFor={inputId} className="sr-only">{copy.placeholder}</label>
        <div className="flex items-end gap-2">
          <textarea
            id={inputId}
            data-testid="chat-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            maxLength={MAX_MESSAGE_CHARS + 50}
            disabled={paused}
            placeholder={copy.placeholder}
            aria-invalid={tooLong || undefined}
            className="aa-focus min-h-[2.75rem] flex-1 resize-none rounded-tile border border-k-line bg-k-bg px-3 py-2 text-sm text-k-ink placeholder:text-k-muted disabled:opacity-60"
          />
          <Button type="submit" size="sm" disabled={paused || sending || !draft.trim() || tooLong} data-testid="chat-send"
            aria-label={copy.send}>
            <Icon name="arrow" className="h-4 w-4" />
            <span className="sr-only sm:not-sr-only">{copy.send}</span>
          </Button>
        </div>
        {tooLong && <p role="alert" className="mt-1 text-xs text-k-danger">{copy.tooLong}</p>}
      </form>
    </section>
  );
}
