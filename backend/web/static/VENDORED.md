# Vendored files

- `htmx.min.js`: htmx 2.0.4, unmodified, from the npm package `htmx.org@2.0.4`
  (`package/dist/htmx.min.js`). The tarball's integrity was checked against the registry's
  `sha512-HLxMCdfXDOJirs3vBZl/ZLoY+c7PfM4Ahr2Ad4YXh6d22T5ltbTXFFkpx9Tgb2vvmWFMbIc3LqN2ToNkZJvyYQ==`.
  Its licence (Zero-Clause BSD) is in `htmx.LICENSE`. To upgrade, change the pinned version here and
  in `tests/web/test_static.py`, and re-run the spike checks in `scripts/frontend_spike/`.

Why 2.0.4 and not a later 2.x: htmx 2.0.5 and later write `htmx-current-path-for-history` to
`sessionStorage` when the script loads, and no setting stops it. The design says the page writes
nothing to web storage. See `docs/reviews/frontend-demo-impl.md`, Section 1.
