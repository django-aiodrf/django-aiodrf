"""
django-rest-knox: no thread hop for requests without a knox token.

Add ``"aiodrf.contrib.knox"`` to ``INSTALLED_APPS``. knox's
``TokenAuthentication`` (and subclasses that keep its ``authenticate()``) is
then only asked, in a thread, when the ``Authorization`` header carries its
prefix. Requests with a token authenticate in one hop as before: knox looks
the token up in the database.
"""
