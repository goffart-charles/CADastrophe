"""Adaptive integration for trimmed native surfaces requiring finer quadrature."""
from math import isfinite


def precise_volume(shape):
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps
    properties = GProp_GProps()
    error = brepgprop.VolumeProperties(shape, properties, 1e-11, True)
    value = properties.Mass()
    if not isfinite(error) or error < 0 or error > 1e-8 or not isfinite(value):
        raise ValueError(f"Impossible de vérifier précisément le volume du solide : erreur {error:g}, volume {value:g}.")
    return value
