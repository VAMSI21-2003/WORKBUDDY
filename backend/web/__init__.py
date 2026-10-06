"""The web page: server-rendered HTML with htmx, in the same process as the API.

Frontend design: ``docs/design/frontend-design.md``. ``mount_web(app)`` adds the page's routes
(``backend.web.routes``) and its static files (``backend/web/static``).
"""

from backend.web.routes import mount_web

__all__ = ["mount_web"]
