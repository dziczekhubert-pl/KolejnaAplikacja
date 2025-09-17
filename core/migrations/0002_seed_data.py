from django.db import migrations
from django.conf import settings
from django.contrib.auth.hashers import make_password


def seed_initial_data(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL)

    if not User.objects.filter(username="admin").exists():
        User.objects.create(
            username="admin",
            email="admin@example.com",
            password=make_password("TwojeHaslo123!"),
            is_staff=True,
            is_superuser=True,
        )

    Cart = apps.get_model("core", "Cart")
    if not Cart.objects.filter(number=999).exists():
        Cart.objects.create(number=999)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),  # dopasuj do pierwszej migracji
    ]

    operations = [
        migrations.RunPython(seed_initial_data),
    ]
