"""Api contracts for the vendor authentication example."""

import pytest
from django.contrib.auth.models import User
from knox.models import AuthToken
from rest_framework_simplejwt.tokens import AccessToken

from aiodrf.utils import run_sync

pytestmark = pytest.mark.django_db(transaction=True)


async def test_vendor_tokens_and_missing_credentials(client):
    user = await User.objects.acreate_user(username="reader", password="local-only")
    jwt = await run_sync(lambda: str(AccessToken.for_user(user)))()
    _, knox = await run_sync(AuthToken.objects.create)(user)
    for endpoint, header in (
        ("jwt", f"Bearer {jwt}"),
        ("knox", f"Token {knox}"),
        ("cookie", f"Bearer {jwt}"),
    ):
        assert (await client.get(f"/{endpoint}/")).status_code == 401
        result = await client.get(f"/{endpoint}/", headers={"Authorization": header})
        assert result.json() == {"username": "reader"}
