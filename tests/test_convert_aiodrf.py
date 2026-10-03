"""django-fastdrf's converter (``manage.py fastdrf_convert``) on aiodrf's serializers."""

from fastdrf import convert

from aiodrf import serializers
from tests.testapp.models import Author


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class ValidatedAuthorSerializer(AuthorSerializer):
    def validate_name(self, value):
        return value


def test_aiodrfs_bases_are_not_the_projects_hooks():
    # aiodrf's serializer bases define validation members (``ais_valid``,
    # ``arun_validation``, ...): registered with django-fastdrf, they are
    # framework code, so only the project's own hooks are reported.
    source = convert.to_msgspec(
        convert.from_serializer(AuthorSerializer(), "Author"), "tests.Author"
    )
    assert "TODO(convert)" not in source
    source = convert.to_msgspec(
        convert.from_serializer(ValidatedAuthorSerializer(), "Author"), "tests.Author"
    )
    todos = {line.strip() for line in source.splitlines() if "TODO(convert)" in line}
    assert todos == {
        "# TODO(convert): ValidatedAuthorSerializer.validate_name() is not converted."
    }
