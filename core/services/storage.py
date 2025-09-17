# core/services/storage.py
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .models import Load, Cart

@transaction.atomic
def put_on_cart(load_id: int, cart_id: int):
    # Zablokuj oba wiersze na czas operacji
    load = Load.objects.select_for_update().get(pk=load_id)
    cart = Cart.objects.select_for_update().get(pk=cart_id)

    if not load.is_in_storage:
        raise ValueError("Ten ładunek nie jest w chłodni (już zdjęty).")

    load.cart = cart
    # Optymistycznie podbij wersję — inni, którzy zaczęli „obok”, zostaną odrzuceni
    current_version = load.version
    affected = (
        Load.objects
        .filter(pk=load.pk, version=current_version)
        .update(cart=cart, version=F("version") + 1, updated_at=timezone.now())
    )
    if affected != 1:
        raise RuntimeError("Wyścig aktualizacji (version mismatch). Powtórz operację.")

@transaction.atomic
def remove_from_storage(load_id: int):
    load = Load.objects.select_for_update().get(pk=load_id)

    if not load.is_in_storage:
        return False  # idempotentnie

    current_version = load.version
    affected = (
        Load.objects
        .filter(pk=load.pk, version=current_version, is_in_storage=True)
        .update(
            is_in_storage=False,
            date_removed=timezone.now(),
            version=F("version") + 1,
            updated_at=timezone.now(),
        )
    )
    if affected != 1:
        raise RuntimeError("Wyścig przy zdejmowaniu z magazynku. Spróbuj ponownie.")
    return True

@transaction.atomic
def pop_next_load_from_cart(cart_id: int):
    """
    Przykład kolejki FIFO na danym wózku przy dużej równoległości:
    bierze 'pierwszy z brzegu' rekord i od razu go blokuje.
    """
    # WITH SKIP LOCKED: nie czekaj na inne transakcje — weź kolejny wolny
    candidate = (
        Load.objects
        .select_for_update(skip_locked=True)
        .filter(cart_id=cart_id, is_in_storage=True)
        .order_by("created_at")
        .first()
    )
    if not candidate:
        return None

    current_version = candidate.version
    updated = (
        Load.objects
        .filter(pk=candidate.pk, version=current_version, is_in_storage=True)
        .update(
            is_in_storage=False,
            date_removed=timezone.now(),
            version=F("version") + 1,
            updated_at=timezone.now(),
        )
    )
    return candidate if updated == 1 else None
