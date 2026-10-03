# Advisor

The advisor is a stronger model the agent consults at decision points. The agent keeps working on its own
model and keeps the decisions; the advisor answers one question with guidance and does no work. It is off by
default. Whether it pays off (cost against pass rate) has not been measured here, so nothing turns it on for
you.

An escalation (`session.escalation_model`) hands the whole session to another model. A consultation does not:
one question goes out, advice comes back, and the session stays where it is.

## Turning it on

```yaml
advisor:
  enabled: true
  model: claude-opus-5-5          # or provider:model, e.g. openai:gpt-5
```

The settings are in the `advisor` table of [configuration.md](configuration.md). The provider of `model` needs
its API key in the environment unless it is the session's own provider (which uses the session's key and base
URL). Only the main agent consults; sub-agents have no `Advisor` tool and never ask on a signal.

## The Advisor tool

With `advisor.enabled: true` the registry holds a tool named `Advisor` with one argument, `question`. Its
description tells the model to use it before a choice between approaches, before a change that is hard to undo,
or when it is stuck after repeated failures, and not for routine steps. The result is the advisor's answer,
followed by how many consultations are left in the run.

## What the advisor is sent

- the question, cut to 2,000 characters;
- the last `advisor.max_context_messages` messages of the conversation (12 by default), never the whole
  history: each message is cut to about 3,000 characters, tool output to about 1,200, a tool call's input to
  300, each keeping its start and end;
- when the loop asks on a signal, which signal prompted it.

Before anything leaves, the text goes through the secret masker of [secret-masking.md](secret-masking.md)
(credential-named environment values, key and token shapes, `password=` style assignments, and
`session.mask_extra_patterns`). It is always on for the advisor, even with `session.mask_secrets: false`. Masked
values are counted in the `secret_masked` signal. The advisor has no tools and cannot see files; masking is a
mitigation, not a boundary, as described there.

The advisor's system prompt asks for a direct answer under about 250 words: the recommendation, the reasons,
the risks and concrete next steps. Its reply is limited to 1,500 output tokens.

## Limits and refusals

At most `advisor.max_calls` consultations per run (3 by default), the tool's and the signal-triggered one
together; a new run starts the count again. A consultation that cannot be made returns a plain message and the
run carries on (the tool result is an error result, so the model sees that no advice came):

- the advisor is off or names no model;
- the call limit of the run is used up;
- a session cost limit (`session.max_cost_usd`) or token limit (`session.max_total_tokens`) is reached;
- no API key for the advisor's provider is set in the environment;
- the request to the advisor's model failed (this one still counts as a call).

## Cost

Each consultation is a request on the advisor's model. It is recorded in the analytics ledger under the agent
type `advisor` (with the turn and the tool that ran before it, like any request), priced for the advisor's
model, added to the session's token totals and to the session's own cost, so it counts against
`session.max_cost_usd` and `session.max_total_tokens` and shows in `/cost`. The cost limit can only count what the
price table knows: for a model without a price the consultation is recorded at zero cost (the token limit
still counts it).

## Asking on a signal

With `advisor.on_signals: true` the escalation check asks the advisor first. When a signal of
`session.escalation_signals` reaches its threshold, the loop puts the question "the run shows trouble (signal
xN)" to the advisor once per session, adds the answer to the conversation as a note
(`[Advisor guidance, asked because of ...]`) and shows `Asked the advisor`. It does not switch models.

- If a signal at its threshold keeps coming after the advice (it has grown since the advisor was asked),
  `session.escalation_model` is switched to as usual, once per session.
- If the advisor cannot answer (any refusal above), the escalation by switching happens at once.
- Without `session.escalation_model` the advice is the only action; without `on_signals` nothing changes in the
  escalation.

The signal-triggered consultation uses one of the run's `max_calls`. Advised consultations are counted in the
`advised` signal of a run result.

## Managed policy

An `advisor.model` that a managed policy's allow or deny list refuses is dropped at startup like the
escalation model, see [managed-policy.md](managed-policy.md).

## Tested and not tested

Tested with a fake one-shot completion function and fake providers (no network): the call cap, context
bounding, tool output truncation, masking, cost and token attribution (ledger rows, session cost, cost limit),
every refusal, the tool and its registration, and the interplay with escalation. Not tested: a live request to
any advisor model, and whether advice improves the pass rate per dollar.
