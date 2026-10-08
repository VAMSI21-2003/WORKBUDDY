# feat/google-sign-in: after review round 2 (MERGE at 75eb95b)

The round-2 reviewer said MERGE and deferred four LOWs. Three are cheap and touch only a document and tests; nothing under `backend/`, `buddy/`, `core/` or `skills/` changed after the reviewed commit (`git diff 75eb95b HEAD -- backend buddy core skills` is empty).

- **R2-L1** (the note called Sign out "a plain form POST" in "The page"): the parenthesis is deleted.
- **R2-L2** (nothing pinned the binder being cleared after a refusal that follows a used-up login): `test_a_refusal_after_the_login_was_used_clears_the_binder`. With mutation N07 applied (no clearing) it fails.
- **R2-L3** (the issuer warning was untested): `test_a_provider_that_is_not_google_is_warned_about_once`. With mutation N08 applied (no warning) it fails.
- **R2-L4** (owner ids don't record the issuer): **deferred**, for the owner. A production server started with a non-Google `WORKBUDDY_OIDC_ISSUER` on a volume that holds real people's sessions would let that issuer's subjects own them. It needs control of the server's environment, the settings document says never to set it on a real server, and the server logs a warning at start when it isn't Google's. The two fixes the reviewer names are to refuse a non-Google issuer in production unless a flag is set, or to put a hash of the issuer in the owner id. The live runs use the production image with a fake issuer, which is why this wasn't done.
