"""Exceptions aiodrf adds to DRF's (``rest_framework.exceptions``)."""

from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException

__all__ = ["PreconditionFailed"]


class PreconditionFailed(APIException):
    """
    A precondition of the request (``If-Match``, ``If-Unmodified-Since``,
    ``If-None-Match`` on a write) does not hold: 412, in DRF's error format.
    """

    status_code = status.HTTP_412_PRECONDITION_FAILED
    default_detail = _("Precondition failed.")
    default_code = "precondition_failed"
