"""Persistent physical material data; geometry remains in millimetres."""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class Material:
    name: str
    density_kg_m3: float

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 200:
            raise ValueError('Le matériau exige un nom de 1 à 200 caractères.')
        if (type(self.density_kg_m3) not in (int, float) or not isfinite(self.density_kg_m3)
                or self.density_kg_m3 <= 0):
            raise ValueError('La masse volumique doit être un nombre fini strictement positif en kg/m³.')
        object.__setattr__(self, 'name', self.name.strip())
        object.__setattr__(self, 'density_kg_m3', float(self.density_kg_m3))

    def mass_grams(self, volume_mm3):
        if not isfinite(volume_mm3) or volume_mm3 <= 0:
            raise ValueError('Le volume doit être strictement positif et fini.')
        return volume_mm3*self.density_kg_m3/1_000_000.


def material_record(material):
    return None if material is None else dict(name=material.name, density_kg_m3=material.density_kg_m3)


def decode_material(data):
    if data is None:
        return None
    if not isinstance(data, dict) or set(data) != {'name', 'density_kg_m3'}:
        raise ValueError('Description du matériau invalide.')
    return Material(**data)
