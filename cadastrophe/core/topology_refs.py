"""Persistent selections. Native topology and indexes are never serialized here."""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class TopologyReference:
    kind: str
    labels: tuple[str, ...] = ()
    geometry: tuple[float, ...] = ()

    def __post_init__(self):
        if self.kind not in {"Face", "Edge", "Vertex"}:
            raise ValueError("Type de référence topologique inconnu.")
        if (type(self.labels) is not tuple or len(self.labels) > 1024
                or len(set(self.labels)) != len(self.labels)
                or any(type(s) is not str or not s or len(s) > 4096 for s in self.labels)):
            raise ValueError("Noms topologiques invalides.")
        if (type(self.geometry) is not tuple or len(self.geometry) > 32
                or any(type(v) not in (int, float) or not isfinite(v) for v in self.geometry)
                or not (self.labels or self.geometry)):
            raise ValueError("Référence topologique vide ou invalide.")


def encode_reference(ref):
    return None if ref is None else dict(kind=ref.kind, labels=list(ref.labels), geometry=list(ref.geometry))


def decode_reference(data):
    if data is None:
        return None
    if (type(data) is not dict or set(data) != {"kind", "labels", "geometry"}
            or type(data["labels"]) is not list or type(data["geometry"]) is not list):
        raise ValueError("Description de référence topologique invalide.")
    return TopologyReference(data["kind"], tuple(data["labels"]), tuple(data["geometry"]))
