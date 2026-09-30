from django.db import migrations, models
from django_mongodb_backend.fields import ObjectIdAutoField


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="Note",
            fields=[
                (
                    "id",
                    ObjectIdAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("title", models.CharField(max_length=100)),
            ],
        )
    ]
