from modeltranslation.translator import TranslationOptions, register
from core import models


@register(models.VehicleCategory)
class VehicleCategoryTranslationOptions(TranslationOptions):
    fields = ('name', 'description')
