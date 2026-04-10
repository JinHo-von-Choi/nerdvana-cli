---
name: compress-context
description: Compress conversation context into a concise handoff summary
trigger: /compress-context
---

CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

<analysis>
Review the conversation and identify:
- The current objective and its current state
- Key decisions made and their rationale
- Errors encountered and how they were resolved
- Pending tasks remaining
- The last assistant response (verbatim if short, summarized if long)
</analysis>

<summary>
Produce a concise handoff document covering:

**Objective**: [What the user is trying to accomplish]

**Progress**: [What has been done so far]

**Key Decisions**: [Architecture, technology, approach choices made]

**Errors & Fixes**: [Any errors encountered and how they were resolved]

**Pending Tasks**: [What still needs to be done]

**Last Response**: [Verbatim or close paraphrase of the last assistant message]
</summary>

REMINDER: Do NOT call any tools. Respond with plain text only.
