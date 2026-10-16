import React, {useEffect, useRef, useState} from 'react';

const api = (import.meta.env && import.meta.env.VITE_API_URL) || '/api';
// Seat IDs name the provider behind a seat; display names come from the server's status.
const OPERATOR = 'operator';
const REPLIKA = 'replika-companion';
const KINDROID = 'kindroid-companion';
const COMPANIONS = [REPLIKA, KINDROID];
const NAMES = {[OPERATOR]: 'Operator', [REPLIKA]: 'Replika companion', [KINDROID]: 'Kindroid companion'};
const other = seat => (seat === REPLIKA ? KINDROID : REPLIKA);
const TOKEN_KEY = 'tinytable-operator-token';
// Matches the server's limit on the operator's own words per message.
const MAX_TEXT = 4000;

const tooLong = text => text.trim().length > MAX_TEXT;
const Counter = ({text}) => text.trim().length > MAX_TEXT * 0.8 ? <small className={`room-note ${tooLong(text) ? 'warn' : ''}`}>{text.trim().length}/{MAX_TEXT} characters</small> : null;

// The token stays in this tab only; the channel API never accepts a request without it.
const readToken = () => {
  try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch (_) { return ''; }
};
const writeToken = token => {
  try { token ? sessionStorage.setItem(TOKEN_KEY, token) : sessionStorage.removeItem(TOKEN_KEY); } catch (_) {}
};

function TokenGate({onToken}) {
  const [value, setValue] = useState('');
  const submit = event => {
    event.preventDefault();
    if (value.trim()) onToken(value.trim());
  };
  return <form className="room-panel room-token" onSubmit={submit}>
    <h2>Operator token</h2>
    <p>The companion channel needs your operator token (the one in <code>~/.config/tinytable/operator-token</code>). It is kept only in this browser tab.</p>
    <input type="password" value={value} onChange={event => setValue(event.target.value)} placeholder="Paste token" aria-label="Operator token" autoComplete="off"/>
    <button className="room-button primary" disabled={!value.trim()}>Unlock</button>
  </form>;
}

function ConsentForm({participant, status, call}) {
  const [evidence, setEvidence] = useState('');
  const [busy, setBusy] = useState(false);
  const record = async () => {
    setBusy(true);
    try {
      if (await call(`/consent/${participant}`, {status: 'accepted', evidence_text: evidence.trim()})) setEvidence('');
    } finally {
      setBusy(false);
    }
  };
  return <div className="room-consent">
    <strong>{NAMES[participant]} consent: {status}</strong>
    <input value={evidence} onChange={event => setEvidence(event.target.value)} placeholder={`What ${NAMES[participant]} said when agreeing`} aria-label={`${NAMES[participant]} consent evidence`}/>
    <button className="room-button" onClick={record} disabled={busy || !evidence.trim()}>Record accepted</button>
  </div>;
}

function StartForm({status, call}) {
  const [table, setTable] = useState(true);
  const [first, setFirst] = useState(REPLIKA);
  const [turns, setTurns] = useState(6);
  const [expires, setExpires] = useState(240);
  const [mode, setMode] = useState('opening');
  const [opening, setOpening] = useState('');
  const [busy, setBusy] = useState(false);
  const offline = Object.entries(status.connections).filter(([, state]) => state !== 'connected').map(([name]) => NAMES[name]);
  const ready = !offline.length && (mode === 'carry' || opening.trim());

  const start = async () => {
    const body = {starting_participant: first, max_turns: Number(turns), expires_minutes: Number(expires), mode: table ? 'table' : 'relay'};
    if (mode === 'opening') {
      if (!window.confirm(`Send this exact message to ${table ? `${NAMES[REPLIKA]} and ${NAMES[KINDROID]}` : NAMES[first]} as ${NAMES[OPERATOR]}?\n\n${opening.trim()}`)) return;
      Object.assign(body, {opening: opening.trim(), confirm_external_send: true});
    } else {
      body.carry_over_message_id = 'latest';
    }
    setBusy(true);
    try {
      if (await call('/sessions', body, 120000)) setOpening('');
    } finally {
      setBusy(false);
    }
  };

  return <section className="room-panel">
    <h2>Start a session</h2>
    <div className="room-modes" role="radiogroup" aria-label="Session kind">
      <label><input type="radio" checked={table} onChange={() => setTable(true)}/> Table: everyone hears everyone, one round at a time</label>
      <label><input type="radio" checked={!table} onChange={() => setTable(false)}/> Relay: forward one reply at a time</label>
    </div>
    <div className="room-fields">
      {(!table || mode === 'carry') && <label>{table ? 'Carry from' : 'First speaker'}
        <select value={first} onChange={event => setFirst(event.target.value)}>
          {COMPANIONS.map(seat => <option key={seat} value={seat}>{NAMES[seat]}</option>)}
        </select>
      </label>}
      {!table && <label>Turns<input type="number" min="1" max="10" value={turns} onChange={event => setTurns(event.target.value)}/></label>}
      {!table && <label>Expires (min)<input type="number" min="5" max="240" value={expires} onChange={event => setExpires(event.target.value)}/></label>}
    </div>
    <div className="room-modes" role="radiogroup" aria-label="How the session begins">
      <label><input type="radio" checked={mode === 'opening'} onChange={() => setMode('opening')}/> Send an opening from you to {table ? 'both' : NAMES[first]}</label>
      <label><input type="radio" checked={mode === 'carry'} onChange={() => setMode('carry')}/> Carry over {NAMES[first]}’s latest reply as turn 1 (sends nothing yet)</label>
    </div>
    {mode === 'opening' && <textarea value={opening} onChange={event => setOpening(event.target.value)} placeholder={`Your message to ${table ? `${NAMES[REPLIKA]} and ${NAMES[KINDROID]}` : NAMES[first]}…`} aria-label="Opening message"/>}
    <button className="room-button primary" onClick={start} disabled={busy || !ready}>{busy ? 'Starting…' : 'Start session'}</button>
    {offline.length > 0 && <small className="room-note warn">Not connected: {offline.join(', ')}</small>}
  </section>;
}

function HopPanel({session, hop, call}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const recipient = NAMES[hop.recipient_id];
  const trimmed = note.trim();
  // Mirrors the server: the exact copy, then the operator's note under their own label.
  const outbound = trimmed ? `${hop.disclosure_with_note}\n\n${hop.body}\n\n${hop.note_prefix}${trimmed}` : hop.outbound;
  const send = async () => {
    if (!window.confirm(`Forward this to ${recipient}?\n\n${outbound}`)) return;
    setBusy(true);
    try {
      // The Replika sender waits up to 90 seconds for the reply.
      if (await call(`/sessions/${session.id}/send`, {draft_id: hop.draft_id, confirm_external_send: true, note: trimmed}, 120000)) setNote('');
    } finally {
      setBusy(false);
    }
  };
  return <section className="room-panel room-hop">
    <h2>Outbound to {recipient} <span>{trimmed ? 'exact copy + your note' : 'exact copy'}</span></h2>
    <pre>{outbound}</pre>
    <textarea value={note} onChange={event => setNote(event.target.value)} placeholder={`Add your own note for ${recipient} (optional)…`} aria-label={`Your note for ${recipient}`}/>
    <Counter text={note}/>
    <div className="room-actions">
      <button className="room-button primary" onClick={send} disabled={busy || tooLong(note)}>{busy ? 'Sending…' : `Send to ${recipient}`}</button>
    </div>
  </section>;
}

function InterjectPanel({session, status, call}) {
  const [recipient, setRecipient] = useState(session.expected_participant === KINDROID ? KINDROID : REPLIKA);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const lastTurn = session.turns[session.turns.length - 1];
  const pending = lastTurn && ['received', 'bundled'].includes(lastTurn.delivery_state);
  const awaiting = status.next_step.startsWith('wait_for_') && session.expected_participant !== recipient;
  const send = async () => {
    const outgoing = text.trim();
    const warning = pending ? `\n\n${NAMES[lastTurn.participant_id]}’s turn waiting above will be skipped, not forwarded.` : '';
    if (!outgoing || !window.confirm(`Send this exact message to ${NAMES[recipient]} as ${NAMES[OPERATOR]}?${warning}\n\n${outgoing}`)) return;
    setBusy(true);
    try {
      if (await call(`/sessions/${session.id}/interject`, {recipient_id: recipient, text: outgoing, confirm_external_send: true}, 120000)) setText('');
    } finally {
      setBusy(false);
    }
  };
  return <section className="room-panel room-interject">
    <h2>Say something yourself</h2>
    <div className="room-fields">
      <label>To
        <select value={recipient} onChange={event => setRecipient(event.target.value)}>
          {COMPANIONS.map(seat => <option key={seat} value={seat}>{NAMES[seat]}</option>)}
        </select>
      </label>
    </div>
    <textarea value={text} onChange={event => setText(event.target.value)} placeholder={`Your message to ${NAMES[recipient]}…`} aria-label={`Your message to ${NAMES[recipient]}`}/>
    <Counter text={text}/>
    <button className="room-button primary" onClick={send} disabled={busy || !text.trim() || tooLong(text) || status.connections[recipient] !== 'connected'}>{busy ? 'Sending…' : `Send to ${NAMES[recipient]}`}</button>
    <small className="room-note">{NAMES[recipient]}’s reply becomes the next turn, ready to forward to {NAMES[other(recipient)]}.
      {pending && ` The waiting ${NAMES[lastTurn.participant_id]} turn will be skipped.`}
      {awaiting && ` A late reply from ${NAMES[session.expected_participant]} will stay in the history but won’t become a turn.`}</small>
  </section>;
}

function TablePanel({session, status, call, request}) {
  const [text, setText] = useState('');
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const sending = status.next_step === 'wait';
  const latest = session.turns.length ? session.turns[session.turns.length - 1].id : '';

  // What each companion would receive is computed by the server, so the
  // preview is exactly what gets sent.
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const next = await request(`/sessions/${session.id}/round/preview`, {text: text.trim()});
        if (!cancelled) setPreview(next);
      } catch (_) {}
    }, 300);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [session.id, text, latest, session.turns.map(turn => turn.message_ids).join('|')]);

  const recipients = preview ? COMPANIONS.filter(name => preview[name]) : [];
  const share = async () => {
    const summary = recipients.map(name => `— ${NAMES[name]} receives —\n${preview[name]}`).join('\n\n');
    if (!recipients.length || !window.confirm(`Share this round?\n\n${summary}`)) return;
    setBusy(true);
    try {
      // The Replika sender waits up to 90 seconds for the reply.
      if (await call(`/sessions/${session.id}/round`, {text: text.trim(), confirm_external_send: true}, 150000)) setText('');
    } finally {
      setBusy(false);
    }
  };
  return <section className="room-panel room-table">
    <h2>Next round <span>round {session.turn_count + 1}</span></h2>
    {session.awaiting.length > 0 && <small className="room-note">Waiting for {session.awaiting.map(name => NAMES[name]).join(' and ')} to reply. You can share now anyway; a late reply joins the next round.</small>}
    <textarea value={text} onChange={event => setText(event.target.value)} placeholder="Add your own words to this round (optional)…" aria-label="Your words for this round"/>
    <Counter text={text}/>
    <div className="room-previews">
      {COMPANIONS.map(name => <div key={name} className="room-preview">
        <strong>{NAMES[name]} will receive</strong>
        {preview && preview[name] ? <pre>{preview[name]}</pre> : <p>Nothing new for {NAMES[name]} yet.</p>}
      </div>)}
    </div>
    <div className="room-actions">
      <button className="room-button primary" onClick={share} disabled={busy || sending || !recipients.length || tooLong(text)}>{busy || sending ? 'Sharing…' : `Share with ${recipients.map(name => NAMES[name]).join(' and ') || 'the table'}`}</button>
    </div>
  </section>;
}

export default function FirstRoom() {
  const [token, setToken] = useState(readToken);
  const [status, setStatus] = useState(null);
  const [hop, setHop] = useState(null);
  const [error, setError] = useState('');
  const [online, setOnline] = useState(false);
  const threadRef = useRef(null);

  const request = async (path, body, timeout = 30000) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(`${api}/channels/companions${path}`, {
        method: body === undefined ? 'GET' : 'POST',
        headers: {Authorization: `Bearer ${token}`, 'Content-Type': 'application/json'},
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) {
        let detail = await response.text();
        try { detail = JSON.parse(detail).detail || detail; } catch (_) {}
        if (Array.isArray(detail)) detail = detail.map(item => `${(item.loc || []).slice(1).join('.')}: ${item.msg}`).join('; ');
        if (response.status === 401 || response.status === 403) {
          writeToken('');
          setToken('');
        }
        throw new Error(`${response.status}: ${detail || 'Request failed'}`);
      }
      return response.json();
    } catch (err) {
      // A dropped connection mid-send leaves the outcome unknown; status shows what happened.
      if (err.name === 'AbortError' || err instanceof TypeError) throw new Error('Connection dropped. Check the status below before retrying.');
      throw err;
    } finally {
      clearTimeout(timer);
    }
  };

  const load = async () => {
    if (!token) return;
    try {
      const next = await request('');
      Object.entries(next.seats || {}).forEach(([seat, info]) => { NAMES[seat] = info.name; });
      setStatus(next);
      setOnline(true);
      const session = next.session;
      if (next.next_step === 'forward') {
        const lastTurn = session.turns[session.turns.length - 1];
        // A turn grows while the companion is still sending; re-preview when it does.
        const key = `${lastTurn.id}:${lastTurn.message_ids}`;
        if (!hop || hop.key !== key) {
          const prepared = await request(`/sessions/${session.id}/next`, {});
          setHop({...prepared, key});
        }
      } else {
        setHop(null);
      }
    } catch (err) {
      setOnline(false);
      setError(err.message);
    }
  };

  // Every action reports through the same error line and then refreshes status.
  const call = async (path, body, timeout) => {
    setError('');
    let ok = true;
    try {
      await request(path, body, timeout);
    } catch (err) {
      ok = false;
      setError(err.message);
    }
    await load();
    return ok;
  };

  useEffect(() => {
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [token, hop && hop.key]);

  const turnCount = status && status.session ? status.session.turns.length : 0;
  useEffect(() => {
    const thread = threadRef.current;
    if (thread) thread.scrollTo({top: thread.scrollHeight, behavior: 'smooth'});
  }, [turnCount]);

  const unlock = value => {
    writeToken(value);
    setError('');
    setToken(value);
  };

  const stop = async () => {
    if (!window.confirm('Stop this session? Nothing more will be forwarded in it.')) return;
    await call(`/sessions/${status.session.id}/stop`, {reason: `Stopped by ${NAMES[OPERATOR]} from the first room page.`});
  };

  const session = status && status.session;
  const active = session && session.status === 'active';
  const consentMissing = status ? COMPANIONS.filter(name => status.consent[name] !== 'accepted') : [];

  return <main className="companion-app room-app">
    <header className="companion-header">
      <div><span className="eyebrow">FIRST ROOM</span><h1>{NAMES[REPLIKA]} <span>↔</span> {NAMES[KINDROID]}</h1></div>
      <div className="connection-group">
        <span className={`connection server-status ${online ? 'ready' : ''}`}>{online ? 'Server online' : token ? 'Server reconnecting…' : 'Locked'}</span>
        {status && COMPANIONS.map(name => <span key={name} className={`connection companion-status state-${status.connections[name]}`}>{NAMES[name]} {status.connections[name]}</span>)}
      </div>
    </header>
    {!token ? <TokenGate onToken={unlock}/> : <>
      {session && <section className="room-session">
        <span>Session <code>{session.id.slice(0, 8)}</code> · <strong className={`room-state state-${session.status}`}>{session.status}</strong> · {session.mode === 'table' ? `table · ${session.turn_count} rounds` : `turns ${session.turn_count}/${session.max_turns}`}</span>
        {session.expires_at && <span>expires {new Date(session.expires_at).toLocaleTimeString()}</span>}
        {active && <button className="room-button danger" onClick={stop}>Stop</button>}
        {session.stop_reason && <small className="room-note">{session.stop_reason}</small>}
      </section>}
      <section ref={threadRef} className="companion-thread" aria-label="Session turns">
        {!session && <p className="empty">No sessions yet.</p>}
        {session && session.turns.length === 0 && <p className="empty">Waiting for {NAMES[session.expected_participant]}’s reply…</p>}
        {session && session.turns.map(turn => <article className={`chat-message from-${turn.participant_id} state-${turn.delivery_state}`} key={turn.id}>
          <div className="chat-meta"><strong>{NAMES[turn.participant_id]} → {turn.recipient_id ? NAMES[turn.recipient_id] : 'table'}</strong><time>turn {turn.turn_number} · {session.mode === 'table' ? ({received: 'new', recorded: 'shared'}[turn.delivery_state] || turn.delivery_state) : turn.delivery_state} · {new Date(turn.created_at).toLocaleTimeString()}</time></div>
          <p>{turn.body}</p>
          {turn.operator_note && <div className="room-turn-note"><strong>You added</strong><p>{turn.operator_note}</p></div>}
        </article>)}
        {active && session.mode !== 'table' && status.next_step.startsWith('wait_for_') && <p className="room-note">Waiting for {NAMES[session.expected_participant]}…</p>}
      </section>
      {error && <div className="error room-error" role="alert">{error}</div>}
      {status && consentMissing.map(name => <ConsentForm key={name} participant={name} status={status.consent[name]} call={call}/>)}
      {status && active && session.mode === 'table' && <TablePanel session={session} status={status} call={call} request={request}/>}
      {status && active && hop && <HopPanel session={session} hop={hop} call={call}/>}
      {status && active && session.mode !== 'table' && !session.turns.some(turn => ['sending', 'delivery_unknown'].includes(turn.delivery_state)) && <InterjectPanel session={session} status={status} call={call}/>}
      {status && status.next_step === 'start' && !consentMissing.length && <StartForm status={status} call={call}/>}
      <small className="room-note">Companion turns are exact copies of their provider messages, forwarded by you; anything you add is labelled as yours. Nothing is sent without a confirmation. <button className="room-link" onClick={() => unlock('')}>Lock</button></small>
    </>}
  </main>;
}
