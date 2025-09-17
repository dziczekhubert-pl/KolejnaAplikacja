# core/validators.py
from decimal import Decimal
from django.core.exceptions import ValidationError


def validate_half_kg(value):
    # Dopuszczamy tylko wielokrotność 0.5 (czyli x*2 jest liczbą całkowitą)
    q = (Decimal(value) * 2) % 1
    if q != 0:
        raise ValidationError(
            "Waga musi być wielokrotnością 0,5 kg (np. 123.0 lub 123.5).")
