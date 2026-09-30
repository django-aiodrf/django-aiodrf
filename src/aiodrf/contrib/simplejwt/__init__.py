"""
djangorestframework-simplejwt: no thread hop for requests without a token.

Add ``"aiodrf.contrib.simplejwt"`` to ``INSTALLED_APPS``. ``JWTAuthentication``
(and subclasses that keep its ``authenticate()``) is then only asked, in a
thread, when the request carries a token; without one aiodrf knows the answer
is None. Requests with a token authenticate in one hop as before: verifying
it is CPU work, but loading the user is a query.

``JWTStatelessUserAuthentication`` loads no user. Whether it does I/O depends
on the project's settings (``JWK_URL`` fetches keys over HTTP, the blacklist
app queries), so aiodrf does not declare it pure; a project that knows its
configuration can::

    from aiodrf.utils import async_safe

    @async_safe
    class StatelessJWTAuthentication(JWTStatelessUserAuthentication):
        def authenticate(self, request):
            return super().authenticate(request)
"""
