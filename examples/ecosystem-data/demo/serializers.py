"""Vendor serializer classes keep their own validation and save contracts."""

from dataclasses import dataclass

from django_pydantic_field.rest_framework import SchemaField
from djmoney.contrib.django_rest_framework import MoneyField
from drf_extra_fields.fields import Base64ImageField
from drf_extra_fields.relations import PresentablePrimaryKeyRelatedField
from drf_writable_nested.serializers import WritableNestedModelSerializer
from phonenumber_field.serializerfields import PhoneNumberField
from rest_flex_fields import FlexFieldsModelSerializer
from rest_framework import serializers
from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_polymorphic.serializers import PolymorphicSerializer
from taggit.serializers import TaggitSerializer, TagListSerializerField

from .models import ArchivedNote, ArtProject, Author, Book, Limits, Photo, Project


class AuthorSerializer(serializers.ModelSerializer):
    phone = PhoneNumberField(required=False, allow_blank=True)

    class Meta:
        model = Author
        fields = ["id", "name", "phone"]


class BookSerializer(TaggitSerializer, serializers.ModelSerializer):
    price = MoneyField(max_digits=10, decimal_places=2, min_value=0)
    tags = TagListSerializerField(required=False)
    limits = SchemaField(schema=Limits)

    class Meta:
        model = Book
        fields = ["id", "title", "author", "price", "price_currency", "tags", "limits"]


class NestedBookSerializer(WritableNestedModelSerializer):
    author = AuthorSerializer()

    class Meta:
        model = Book
        fields = ["id", "title", "author"]


class ExpandedBookSerializer(FlexFieldsModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title", "author"]
        expandable_fields = {"author": AuthorSerializer}


class PhotoSerializer(serializers.ModelSerializer):
    image = Base64ImageField()
    author = PresentablePrimaryKeyRelatedField(
        queryset=Author.objects.all(), presentation_serializer=AuthorSerializer
    )

    class Meta:
        model = Photo
        fields = ["id", "image", "author"]


class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ArchivedNote
        fields = ["id", "text"]


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ["id", "topic"]


class ArtSerializer(serializers.ModelSerializer):
    class Meta:
        model = ArtProject
        fields = ["id", "topic", "artist"]


class ProjectTypes(PolymorphicSerializer):
    model_serializer_mapping = {Project: ProjectSerializer, ArtProject: ArtSerializer}


@dataclass
class Address:
    city: str
    postal_code: str


class AddressSerializer(DataclassSerializer):
    class Meta:
        dataclass = Address


class SubmissionSerializer(serializers.Serializer):
    author = AuthorSerializer()
    attachment = serializers.FileField()
