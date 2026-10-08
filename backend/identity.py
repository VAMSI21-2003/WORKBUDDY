"""Identity — who a request is from, as the rest of the app sees it.

A ``Principal`` is what ``backend.access.check_credential`` answers with for a valid request.
Its ``owner`` is the string that is stored with a session (``owner.json``), compared when a
session is asked for, and used to key the per-person rate limits. Nothing else about a person is
ever stored with a session.

==========  ==========================  ==============================================
kind        owner                       who
==========  ==========================  ==============================================
google      ``google:<subject id>``     a person who signed in with Google
operator    ``operator``                a script holding ``WORKBUDDY_OPERATOR_TOKEN``
demo        ``demo``                    the shared demo password (never in production)
dev         ``dev``                     no credential at all (``WORKBUDDY_ENV=dev`` only)
==========  ==========================  ==============================================

The shared kinds (``demo``, ``dev``) are one principal each: everyone who holds the password, or
everyone on a dev server, is the same owner. That is what those modes always were.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PrincipalKind = Literal["google", "operator", "demo", "dev"]


@dataclass(frozen=True)
class Principal:
    owner: str
    kind: PrincipalKind
    email: str | None = None

    @classmethod
    def google(cls, subject: str, email: str) -> Principal:
        return cls(owner=f"google:{subject}", kind="google", email=email)


OPERATOR = Principal(owner="operator", kind="operator")
DEMO = Principal(owner="demo", kind="demo")
DEV = Principal(owner="dev", kind="dev")
