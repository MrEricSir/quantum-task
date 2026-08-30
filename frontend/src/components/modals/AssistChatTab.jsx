import { CopyIcon, CheckIcon, TrashIcon } from '@radix-ui/react-icons'

// "Chat" tab content for AssistModal. All state/handlers come from useAssistChat --
// this component is presentational only.
export default function AssistChatTab({ task, chat }) {
  const {
    messages, context, output, input, setInput, sending, streaming, threadErr, searching,
    showContext, editContext, setEditContext, savingCtx, loadingCtxSrc, ctxLoadedFrom,
    savingOutput, copied, copiedOutput,
    scrollRef, inputRef,
    send, handleKeyDown,
    handleToggleContext, handleCancelContext, saveContext, loadContextFrom,
    handleSaveOutput, handleCopyOutput, handleClearOutput, handleCopy,
    handleClearThread,
  } = chat

  const hasHistory = messages.length > 0

  return (
    <>
      {/* Saved output panel */}
      {output && (
        <div className="assist-output-panel">
          <div className="assist-output-panel-header">
            <span className="assist-label">Saved output</span>
            <button className="assist-icon-btn" onClick={handleCopyOutput} title="Copy">
              {copiedOutput ? <CheckIcon /> : <CopyIcon />}
            </button>
            <button className="assist-icon-btn" onClick={handleClearOutput} title="Remove saved output">
              <TrashIcon />
            </button>
          </div>
          <div className="assist-output-panel-text">{output}</div>
        </div>
      )}

      {/* Context panel (collapsible) */}
      <div className="assist-context-panel">
        <button
          className="assist-context-toggle"
          onClick={handleToggleContext}
        >
          <span>Context</span>
          <span className={`assist-context-caret${showContext ? ' assist-context-caret--open' : ''}`}>▾</span>
          {context && <span className="assist-context-dot" />}
        </button>
        {showContext && (
          <div className="assist-context-body">
            <div className="assist-context-source-row">
              <span className="assist-context-source-label">Load from</span>
              <select
                className="assist-context-source-select"
                defaultValue=""
                onChange={loadContextFrom}
                disabled={loadingCtxSrc}
              >
                <option value="" disabled>Choose a source…</option>
                <optgroup label="Sections">
                  <option value="section:today">Today's tasks</option>
                  <option value="section:week">This week's tasks</option>
                  <option value="section:month">This month's tasks</option>
                </optgroup>
                {(task.tags ?? []).length > 0 && (
                  <optgroup label="Tags">
                    {(task.tags ?? []).map(tag => (
                      <option key={tag.id} value={`tag:${tag.id}`}>{tag.name} tasks</option>
                    ))}
                  </optgroup>
                )}
                <optgroup label="Semantic">
                  <option value="similar">Similar cards</option>
                </optgroup>
              </select>
              {loadingCtxSrc && <span className="assist-context-loading">…</span>}
            </div>
            {ctxLoadedFrom && (
              <div className="assist-context-loaded-note">{ctxLoadedFrom}</div>
            )}
            <textarea
              className="assist-context-input"
              placeholder="Paste an email, document, or any reference text here — or load cards above. The assistant will use it throughout the conversation."
              value={editContext}
              onChange={e => setEditContext(e.target.value)}
              rows={5}
            />
            <div className="assist-context-actions">
              {editContext && (
                <button className="assist-copy" onClick={() => setEditContext('')}>Clear</button>
              )}
              <button className="assist-copy" onClick={handleCancelContext}>Cancel</button>
              <button className="assist-run assist-run--sm" onClick={saveContext} disabled={savingCtx}>
                {savingCtx ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Message thread */}
      <div className="assist-thread" ref={scrollRef}>
        {hasHistory && (
          <div className="assist-thread-clear-row">
            <button className="assist-clear-btn" onClick={handleClearThread}>Clear conversation</button>
          </div>
        )}
        {!hasHistory && (
          <div className="assist-thread-empty">
            Start a conversation about this task — the assistant will remember it.
          </div>
        )}
        {messages.map((msg, idx) => (
          <div key={idx} className={`assist-msg assist-msg--${msg.role}`}>
            <div className="assist-msg-bubble">
              {msg._streaming && !msg.content
                ? <span className="assist-msg-placeholder">{searching ? 'Searching the web…' : 'Thinking…'}</span>
                : <span className="assist-msg-text">{msg.content}</span>
              }
            </div>
            {msg.role === 'assistant' && !msg._streaming && msg.content && (
              <div className="assist-msg-actions">
                <button
                  className="assist-msg-action"
                  onClick={() => handleCopy(msg.content, idx)}
                  title="Copy"
                >
                  {copied === idx ? <CheckIcon /> : <CopyIcon />}
                </button>
                <button
                  className="assist-msg-action"
                  onClick={() => handleSaveOutput(msg.content, idx)}
                  disabled={savingOutput === idx}
                  title="Save as output"
                >
                  {savingOutput === idx ? '…' : output === msg.content ? '✓ Saved' : 'Save'}
                </button>
              </div>
            )}
          </div>
        ))}
        {threadErr && <div className="assist-thread-error">{threadErr}</div>}
      </div>

      {/* Input */}
      <div className="assist-input-row">
        <textarea
          ref={inputRef}
          className="assist-input"
          placeholder="Message…"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={2}
          disabled={sending || streaming}
        />
        <button
          className="assist-send"
          onClick={send}
          disabled={!input.trim() || sending || streaming}
        >
          {sending || streaming ? <span className="assist-spinner" /> : '↑'}
        </button>
      </div>
    </>
  )
}
