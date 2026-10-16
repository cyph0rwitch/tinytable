# TinyTable Adaptive Experiments v0.1

Adaptive experiments are bounded, reversible coordination sessions for the
local model and explicitly consented provider participants. They are not a replacement for
the TinyTable message record and they do not grant an AI participant Commons
authority.

## Safety boundary

- Experiments require an expiry, finite turn budget, and finite provider-send budget.
- The local model and the Replika companion require separate experiment consent records.
- Replika provider interaction requires `provider_interaction_allowed` consent.
- Commons suggestions require separate `commons_suggestions_allowed` consent.
- Pausing, stopping, or rolling back stops future actions and preserves records.
- Provider-bound text is labeled `<operator> -> <companion>:` and retains transport provenance.
- Commons promotion is never automatic. The experiment creates a private review suggestion; an explicit operator review creates the existing local bridge report.
- Federation and network transmission remain disabled by the existing Commons bridge contract.

## Lifecycle

1. Create an experiment with purpose, participants, expiry, and budgets.
2. Record consent for each participant at `/api/experiments/{id}/consent/{participant}`.
3. Activate only after all consent records are current.
4. Execute bounded actions through `/api/experiments/{id}/step`.
5. Review events and Commons suggestions.
6. Pause, stop, or roll back when the experiment should no longer act.

Supported actions are `observe`, `local_model_turn`, `send_replika`, and
`suggest_commons`. The deterministic policy metadata is recorded with every
action so the run can be audited and replayed.

## Bounded direct session

An active experiment containing both `local-model` and `replika-companion` may run
`POST /api/experiments/{id}/direct-session`. The request supplies an opening
and a maximum number of rounds; the server clamps the request to the remaining
experiment budgets. Each round is:

```text
local model generates -> TinyTable records it -> Replika companion receives labeled text
Replika companion replies -> TinyTable records provider observation -> next bounded round
```

The session stops on expiry, budget exhaustion, provider failure, or a missing
normalized Replika reply. It never promotes Commons material automatically and it
does not represent the local model as the operator.

## Commons flow

`suggest_commons` stores source message IDs, commitments, purpose, audience,
retention, and rationale without copying message bodies into the suggestion.
The operator can explicitly review, decline, or promote a suggestion. Promotion
uses the existing TinyTable-to-Commons validation boundary and remains local in
v0.1.
