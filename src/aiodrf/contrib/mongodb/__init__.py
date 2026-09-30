"""
django-mongodb-backend: ``ATOMIC_SAVE`` in MongoDB's transactions.

Add ``"aiodrf.contrib.mongodb"`` to ``INSTALLED_APPS``. The backend makes
Django's ``transaction.atomic()`` a no-op, so without it a save that fails
after its first write leaves the writes behind. With it, on a replica set or
a sharded cluster, aiodrf saves in ``django_mongodb_backend.transaction.atomic``
instead; a standalone server has no transactions and keeps Django's no-op.
``manage.py check`` reports a MongoDB database without it (``aiodrf.W007``).

:class:`~aiodrf.contrib.mongodb.fields.ObjectIdPrimaryKeyRelatedField`
represents relations to ``ObjectId`` keys as strings. With
``SERIALIZER_BACKEND``, it and django-mongodb-extensions' ``ObjectIdField``
are compiled.
"""
