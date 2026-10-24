"""Resolve each section at its own producer state before creating one effect."""
from cadastrophe.core.loft import loft_faces


def generating_loft(service, document, feature, trace):
    faces, sources, tolerances = [], [], []
    states = getattr(document, "rebuild_states", {})
    for section in feature.parameters.sections:
        inputs = {ref.body_id: states[ref].shape for ref in section.source.supports if ref in states}
        native = service.region_faces(document, section.source.sketch_id, contour_ids=section.contours, support_shapes=inputs)
        if len(native.regions) != 1:
            raise ValueError("Choisissez une seule région fermée pour chaque section du lissage.")
        faces.append(native.regions[0].face)
        sources.append(service._profiles.source_edges(document, section.source.sketch_id))
        tolerances.append(native.tolerance)
    return loft_faces(tuple(faces), tuple(sources), feature.parameters, trace=trace, tolerance=max(tolerances))
