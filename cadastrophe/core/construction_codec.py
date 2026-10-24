"""Shared activation fields for current construction recipe codecs."""
from dataclasses import replace

from .feature_objects import ConstructionFeature


def with_activation(codec):
    if not issubclass(codec.object_class, ConstructionFeature):
        raise ValueError("Le codec d'activation exige une fonction de construction.")

    def encode(obj):
        return dict(codec.encode(obj), enabled=obj.enabled)

    def decode(base, data):
        if "enabled" not in data or type(data["enabled"]) is not bool:
            raise ValueError("Activation de fonction absente ou invalide.")
        recipe = {key: value for key, value in data.items() if key != "enabled"}
        return replace(codec.decode(base, recipe), enabled=data["enabled"])

    return replace(codec, version=codec.version+1, encode=encode, decode=decode)
