"""Create named local tenants without modifying pre-existing tenant metadata."""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from customers.models import Customer, Domain


class Command(BaseCommand):
    help = "Create alpha.example.test and beta.example.test for local use."

    def handle(self, **options):
        connection.set_schema_to_public()
        for name in ("alpha", "beta"):
            if (
                Customer.objects.filter(schema_name=name).exists()
                or Domain.objects.filter(domain=f"{name}.example.test").exists()
            ):
                raise CommandError(
                    f"Tenant {name} already exists; no existing tenant is overwritten."
                )
        for name in ("alpha", "beta"):
            tenant = Customer.objects.create(schema_name=name, name=name.title())
            Domain.objects.create(
                domain=f"{name}.example.test", tenant=tenant, is_primary=True
            )
            self.stdout.write(f"Created {name}.example.test")
