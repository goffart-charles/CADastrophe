"""User material catalog, independent of material snapshots saved in projects."""
import json
import os
from pathlib import Path
import tempfile

from .materials import Material, decode_material, material_record


# Typical densities, editable in the assignment form for a particular grade.
BUILTIN_MATERIALS = (
    Material('Acier', 7850), Material('Acier inoxydable', 8000),
    Material('Aluminium', 2700), Material('Cuivre', 8960),
    Material('Laiton', 8500), Material('Titane', 4500),
    Material('ABS', 1040), Material('PETG', 1270), Material('PLA', 1240),
)


class MaterialLibrary:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else None
        self.custom_materials = ()
        self.error = ''
        if self.path is not None:
            self._load()

    @property
    def materials(self):
        return BUILTIN_MATERIALS + self.custom_materials

    def _validate(self, materials):
        if len(materials) > 1000:
            raise ValueError('La bibliothèque accepte au maximum 1 000 matériaux personnels.')
        names = {material.name.casefold() for material in BUILTIN_MATERIALS}
        for material in materials:
            if not isinstance(material, Material):
                raise ValueError('Description du matériau invalide.')
            if material.name.casefold() in names:
                raise ValueError('Un matériau porte déjà ce nom ; choisissez un autre nom.')
            names.add(material.name.casefold())

    def _load(self):
        try:
            if not self.path.exists():
                return
            if self.path.stat().st_size > 1024 * 1024:
                raise ValueError('Bibliothèque de matériaux trop volumineuse.')
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if (type(data) is not dict or set(data) != {'schema_version', 'materials'}
                    or type(data['schema_version']) is not int or data['schema_version'] != 1
                    or type(data['materials']) is not list):
                raise ValueError('Bibliothèque de matériaux invalide ou version non prise en charge.')
            materials = tuple(decode_material(record) for record in data['materials'])
            self._validate(materials)
            self.custom_materials = materials
        except (OSError, ValueError, TypeError, OverflowError, RecursionError) as exc:
            self.error = str(exc)

    def add(self, material):
        """Persist first; leave both the catalog and project unchanged on failure."""
        if self.error:
            raise ValueError('La bibliothèque invalide a été conservée : ' + self.error)
        materials = self.custom_materials + (material,)
        self._validate(materials)
        if self.path is not None:
            payload = json.dumps(dict(schema_version=1, materials=[material_record(m) for m in materials]),
                                 ensure_ascii=False, indent=2, allow_nan=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                                 prefix='.materials-', suffix='.tmp', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                temporary = None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        self.custom_materials = materials
