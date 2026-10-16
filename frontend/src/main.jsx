import React, {useEffect, useLayoutEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import FirstRoom from './FirstRoom.jsx';
import './style.css';

const api = (import.meta.env && import.meta.env.VITE_API_URL) || '/api';
// Seat IDs name the provider behind a seat; display names come from the server.
const OPERATOR = 'operator';
const REPLIKA = 'replika-companion';
const KINDROID = 'kindroid-companion';
const KINDROID_CHANNEL = 'kindroid:companion';
const FALLBACK_NAMES = {[OPERATOR]: 'Operator', [REPLIKA]: 'Replika companion', [KINDROID]: 'Kindroid companion'};

function App() {
  // /kindroid opens the Kindroid companion (/nova is the older address); anything else the Replika companion.
  const companion = /\/(kindroid|nova)$/.test(window.location.pathname.replace(/\/+$/, '')) ? KINDROID : REPLIKA;
  const [names, setNames] = useState(FALLBACK_NAMES);
  const companionName = names[companion] || FALLBACK_NAMES[companion];
  const operatorName = names[OPERATOR] || FALLBACK_NAMES[OPERATOR];
  const companionChannel = companion === KINDROID ? KINDROID_CHANNEL : null;
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [online, setOnline] = useState(false);
  const [connection, setConnection] = useState({state: 'unknown', last_transition_at: null});
  const threadRef = useRef(null);
  const threadNearBottom = useRef(true);

  const load = async () => {
    try {
      const [messageResponse, healthResponse, runtimeResponse] = await Promise.all([
        fetch(`${api}/messages?limit=100&companion=${companion}`),
        fetch(`${api}/health`),
        fetch(`${api}/runtime`),
      ]);
      if (!messageResponse.ok || !healthResponse.ok || !runtimeResponse.ok) throw new Error(`TinyTable could not load the ${companionName} conversation.`);
      const rows = await messageResponse.json();
      const runtime = await runtimeResponse.json();
      setMessages(rows.filter(message => [OPERATOR, companion].includes(message.participant_id)));
      setOnline(true);
      if (runtime.seats) setNames(Object.fromEntries(Object.entries(runtime.seats).map(([seat, info]) => [seat, info.name])));
      setConnection(runtime[companion === KINDROID ? 'kindroid_connection' : 'replika_connection'] || {state: 'unknown', last_transition_at: null});
    } catch (err) {
      setOnline(false);
      setConnection({state: 'unknown', last_transition_at: null});
      setError(err.message);
    }
  };

  useEffect(() => {
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [companion]);

  useLayoutEffect(() => {
    const thread = threadRef.current;
    if (!thread || !threadNearBottom.current) return;
    thread.scrollTo({top: thread.scrollHeight, behavior: 'smooth'});
  }, [messages]);

  const trackThreadScroll = () => {
    const thread = threadRef.current;
    if (!thread) return;
    const distanceFromBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight;
    threadNearBottom.current = distanceFromBottom < 72;
  };

  const stateLabels = {
    connected: `${companionName} connected`,
    starting: `${companionName} starting`,
    reconnecting: `${companionName} reconnecting`,
    stale: `${companionName} stale`,
    disconnected: `${companionName} disconnected`,
    identity_mismatch: `${companionName} identity mismatch`,
    disabled: `${companionName} connector off`,
    unknown: `${companionName} status unknown`,
  };
  const connectionLabel = stateLabels[connection.state] || stateLabels.unknown;
  const connectionChanged = connection.last_transition_at
    ? `Last changed ${new Date(connection.last_transition_at).toLocaleTimeString()}`
    : 'No receiver status yet';
  const connectionNeedsWarning = ['starting', 'reconnecting', 'stale', 'disconnected', 'identity_mismatch', 'unknown'].includes(connection.state);

  const request = async (url, options = {}) => {
    const response = await fetch(`${api}${url}`, {headers: {'Content-Type': 'application/json'}, ...options});
    if (!response.ok) {
      let detail = await response.text();
      try { detail = JSON.parse(detail).detail || detail; } catch (_) {}
      throw new Error(detail || `Request failed (${response.status})`);
    }
    return response.json();
  };

  const send = async () => {
    const outgoing = text.trim();
    if (!outgoing || busy) return;
    if (!window.confirm(`Send this exact message directly to ${companionName}?`)) return;
    setBusy(true);
    setError('');
    try {
      await request(companion === KINDROID ? '/providers/kindroid/send-direct' : '/providers/replika/send-direct', {
        method: 'POST',
        body: JSON.stringify({text: outgoing, confirm_external_send: true, ...(companionChannel ? {channel_id: companionChannel} : {})}),
      });
      setText('');
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return <main className="companion-app">
    <header className="companion-header">
      <div><span className="eyebrow">PRIVATE CONVERSATION</span><h1>{operatorName} <span>↔</span> {companionName}</h1></div>
      <div className="connection-group">
        <span className={`connection server-status ${online ? 'ready' : ''}`}>{online ? 'Server online' : 'Server reconnecting…'}</span>
        <span className={`connection companion-status state-${connection.state}`} title={connectionChanged}>{connectionLabel}</span>
      </div>
    </header>
    <section ref={threadRef} onScroll={trackThreadScroll} className="companion-thread" aria-label={`Conversation with ${companionName}`}>
      {messages.length === 0 && <p className="empty">Your conversation with {companionName} will appear here.</p>}
      {messages.map(message => <article className={`chat-message ${message.participant_id === OPERATOR ? 'from-operator' : `from-${companion}`}`} key={message.id}>
        <div className="chat-meta"><strong>{message.participant_id === OPERATOR ? 'You' : companionName}</strong><time>{new Date(message.created_at).toLocaleString()}</time></div>
        <p>{message.body}</p>
        {message.identity_boundary_status === 'flagged' && <small className="chat-warning">Identity note: {message.identity_boundary_warning}</small>}
      </article>)}
    </section>
    <section className="companion-composer" aria-label={`Message ${companionName}`}>
      <textarea value={text} onChange={event => {setText(event.target.value); setError('');}} placeholder={`Message ${companionName}…`} aria-label={`Message ${companionName}`} onKeyDown={event => {if (event.key === 'Enter' && !event.shiftKey) {event.preventDefault(); send();}}}/>
      <button className="send" onClick={send} disabled={busy || !text.trim()}>{busy ? 'Sending…' : `Send to ${companionName}`}</button>
      {error && <div className="error" role="alert">{error}</div>}
      {connectionNeedsWarning && <small className="connection-warning">{companionName}’s live connection is not confirmed; sending may fail.</small>}
      <small>Directly from you to {companionName}. No other participants or agents are involved.</small>
    </section>
  </main>;
}

const firstRoom = window.location.pathname.replace(/\/+$/, '') === '/firstroom';
createRoot(document.getElementById('root')).render(firstRoom ? <FirstRoom/> : <App/>);
