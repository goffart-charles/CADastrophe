"""Resolve physical measurements to owning bodies, without geometric guessing."""
from cadastrophe.plugin_api import ObjectType, SelectionItem
from cadastrophe.core.feature_objects import ConstructionFeature, SketchSourceObject
from .geometry import MeasurementError


def body_items(document, items):
    keys = set()
    for item in items:
        obj = document.objects.get(item.object_id)
        if obj is None or obj.revision != item.revision:
            raise MeasurementError("La sélection n’est plus valide ; sélectionnez à nouveau les éléments.")
        if obj.kind is ObjectType.BODY:
            owners = (obj.id,)
        elif isinstance(obj, ConstructionFeature):
            owners = (obj.body_id,)
        elif isinstance(obj, SketchSourceObject):
            owners = tuple(feature.body_id for feature in document.objects.values()
                           if isinstance(feature, ConstructionFeature)
                           and any(source.sketch_id == obj.id for source, _ in feature.profile_sections))
            owners = owners or obj.body_dependencies
        else:
            owners = ()
        if not owners:
            raise MeasurementError("Sélectionnez un corps, une de ses faces ou une fonction liée à un corps.")
        keys.update(owners)
    return tuple(SelectionItem(obj.id, "Objet", obj.shape, obj.revision)
                 for obj in document.objects.values() if obj.id in keys and obj.kind is ObjectType.BODY)
