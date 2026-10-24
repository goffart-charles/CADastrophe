"""Derived display frames for datum recipes; the document remains authoritative."""
from dataclasses import replace

from cadastrophe.core.reference_objects import DatumPlaneObject
from cadastrophe.core.reference_geometry import resolve_plane, plane_shape


def refresh_datums(document):
    for obj in tuple(document.objects.values()):
        if not isinstance(obj, DatumPlaneObject):
            continue
        try:
            plane = resolve_plane(document, obj)
            if plane == obj.plane and obj.shape is not None and not obj.reference_error_message:
                continue
            current = replace(obj, plane=plane, shape=plane_shape(plane), reference_error_message="", revision=obj.revision+1)
        except (ValueError, RuntimeError) as exc:
            if obj.reference_error_message == str(exc):
                continue
            # Retain the last finite plane resource for the unchanged file
            # schema. It is hidden while invalid, and never used by resolution.
            current = replace(obj, reference_error_message=str(exc), revision=obj.revision+1)
        document._objects[obj.id] = current
        document.object_changed.emit(obj.id, ("plane", "shape", "revision", "reference_error_message"))
