# Buddy's character

Buddy's character, the way Buddy texts and what it may say about work, lives in one file:
**[`buddy/character/buddy.md`](../buddy/character/buddy.md)**. It is the reply writer's system
prompt, loaded as it is by `buddy/writer.py` (inside `buddy/`, because the image ships only
`backend/`, `buddy/`, `core/` and `skills/`). Change the character there, not here.

What the character can't change: every reply is checked by code before it is shown
(`buddy/claims.py`, against the VERIFIED RESULT built by `buddy/facts.py`), and a reply that says
anything the checks don't back is rewritten once, then replaced by the template reply
(`CLAUDE.md`, "Decided behaviour").
