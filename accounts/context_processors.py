from .models import PanelAppearance


def panel_appearance(request):
    return {"panel_language": PanelAppearance.objects.filter(pk=1).values_list("language", flat=True).first() or "fa"}
