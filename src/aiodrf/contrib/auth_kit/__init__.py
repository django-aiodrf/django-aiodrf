"""
drf-auth-kit: no thread hop for requests without a token.

Add ``"aiodrf.contrib.auth_kit"`` to ``INSTALLED_APPS``. auth-kit's
``JWTCookieAuthentication`` and ``TokenCookieAuthentication`` (and subclasses
that keep its ``authenticate()``) are then only asked, in a thread, when the
request carries a token: an ``Authorization: Bearer`` header or, without an
``Authorization`` header, the auth cookie of the request's cookie profile.
Requests with a token authenticate in one hop as before: whatever
``AUTH_TYPE`` says, the token leads to a user, which is a query.
"""
