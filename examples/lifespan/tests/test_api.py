"""Api contracts for the lifespan example."""


async def test_typed_resource(client):
    assert (await client.get("/resource/")).json() == {"value": 42}


def test_command_owns_its_resource():
    from io import StringIO

    from django.core.management import call_command

    output = StringIO()
    call_command("probe", stdout=output)
    assert output.getvalue().strip() == "42"
