"""Error types. Messages are written for the agent reading them."""


class TaigaMcpError(Exception):
    """Base class; str(e) is shown to the calling agent as-is."""


class AuthError(TaigaMcpError):
    """No token, or Taiga rejected it (HTTP 401)."""


class PermissionDenied(TaigaMcpError):
    """Taiga said 403: the agent's role does not allow the action."""


class NotFound(TaigaMcpError):
    pass


class BadRequest(TaigaMcpError):
    """Invalid input (our validation, or Taiga's 400)."""


class VersionConflict(TaigaMcpError):
    """Taiga rejected a PATCH because the item changed under us."""


class TaigaUnavailable(TaigaMcpError):
    pass
