# Buddy's character

Buddy's character, the way Buddy texts and what it may say about work, lives in one file:
**[`buddy/character/buddy.md`](../buddy/character/buddy.md)**. It is the reply writer's system
prompt, loaded as it is by `buddy/writer.py` (inside `buddy/`, because the image ships only
`backend/`, `buddy/`, `core/` and `skills/`). Change the character there, not here.

What the character can't change: on a turn about a file, every fact in a reply is a sentence code wrote (`buddy/facts.py`). The model shows facts only by their slots and writes only social words around them (`buddy/social.py`). On a chat turn it writes freely, and `buddy/claims.py` refuses a reply that names a file, counts rows or says something was done. A refused reply is rewritten once, then replaced by the template reply (`CLAUDE.md`, "Decided behaviour").
