from django.db import migrations
from django.conf import settings
from django.contrib.auth.hashers import make_password

def seed_initial_data(apps, schema_editor):
    # Pobierz model użytkownika zgodnie z AUTH_USER_MODEL
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)

    if not User.objects.filter(username="admin").exists():
        User.objects.create(
            username="admin",
            email="admin@example.com",
            password=make_password("TwojeHaslo123!"),
            is_staff=True,
            is_superuser=True,
        )

    # Przykładowy wózek – opcjonalnie, usuń jeśli nie chcesz
    Cart = apps.get_model("core", "Cart")
    if not Cart.objects.filter(number=999).exists():
        Cart.objects.create(number=999)

def noop_reverse(apps, schema_editor):
    # Nie usuwamy admina przy rollbacku
    pass

class Migration(migrations.Migration):
    dependencies = [
        ("core", "0008_load_initial_weight_kg"),  # KLUCZ: po ostatniej Twojej migracji
    ]

    operations = [
        migrations.RunPython(seed_initial_data, noop_reverse),
    ]
