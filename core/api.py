# core/api.py (DRF lub zwykłe Django)
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from .services.storage import remove_from_storage
from .models import Load


def storage_state(request):
    q = Load.objects.filter(is_in_storage=True).select_related(
        "cart").order_by("cart__number", "created_at")
    data = [{
        "id": l.id,
        "cart": l.cart.number,
        "packing_date": l.packing_date.isoformat(),
        "flavor": l.flavor,
        "product_code": l.product_code,
        "pieces": l.pieces,
        "total_weight_kg": float(l.total_weight_kg) if l.total_weight_kg is not None else None,
    } for l in q]
    return JsonResponse({"loads": data})


@require_POST
def storage_remove(request, load_id: int):
    ok = remove_from_storage(load_id)
    return JsonResponse({"ok": ok})
