# core/tasks.py
import logging
from django.conf import settings
from .models import Cart, Load
from .serializers import cart_to_dict, load_to_dict
from .json_store import atomic_write_json  # z poprzedniej wersji

logger = logging.getLogger(__name__)


def export_snapshot_to_json() -> dict:
    """
    Eksportuje aktualny stan bazy (Cart, Load) do plików JSON.
    Tworzy 3 pliki:
      - carts.json       -> wszystkie wózki
      - loads.json       -> wszystkie ładunki
      - loads_in_storage.json -> tylko te w magazynku (status == IN_COLD_ROOM)

    Zwraca słownik z liczbą wyeksportowanych rekordów.
    """
    try:
        carts = [cart_to_dict(c) for c in Cart.objects.all().order_by("id")]
        loads = [load_to_dict(l) for l in Load.objects.all().order_by("id")]

        atomic_write_json(settings.JSON_FILES["carts"], carts)
        atomic_write_json(settings.JSON_FILES["loads"], loads)

        in_storage = [l for l in loads if l.get("is_in_storage")]
        atomic_write_json(settings.JSON_DATA_DIR /
                          "loads_in_storage.json", in_storage)

        result = {
            "carts": len(carts),
            "loads": len(loads),
            "in_storage": len(in_storage),
        }
        logger.info("Snapshot zapisany do JSON: %s", result)
        return result

    except Exception as e:
        logger.exception("Błąd przy eksporcie snapshotu do JSON: %s", e)
        return {"error": str(e)}
