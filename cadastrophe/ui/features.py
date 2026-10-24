"""CADastrophe tool families, grouped by the modeling task.

These are unavailable commands, not implementations of modeling operations.
Each column contains one large tool or a compact pair/triple, in visual order.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureTool:
    key: str
    title: str
    caption: str
    icon: str


FEATURE_TOOLS = (
    FeatureTool("extrude", "Ajout par extrusion", "Ajouter\npar extrusion", "extrude"),
    FeatureTool("revolve", "Révolution additive", "Révolution +", "revolve"),
    FeatureTool("sweep", "Balayage additif", "Balayage +", "sweep"),
    FeatureTool("loft", "Volume lissé", "Lissage +", "loft"),
    FeatureTool("boundary", "Volume par frontières", "Frontières +", "boundary"),
    FeatureTool("cut_extrude", "Découpe extrudée", "Découper\npar extrusion", "cut-extrude"),
    FeatureTool("hole_wizard", "Perçage guidé", "Perçage guidé", "hole-wizard"),
    FeatureTool("thread", "Filetage / taraudage", "Filetage", "sweep"),
    FeatureTool("cut_revolve", "Révolution soustractive", "Révolution −", "cut-revolve"),
    FeatureTool("cut_sweep", "Découpe par balayage", "Balayage −", "cut-sweep"),
    FeatureTool("cut_loft", "Découpe lissée", "Lissage −", "cut-loft"),
    FeatureTool("cut_boundary", "Découpe par frontières", "Frontières −", "cut-boundary"),
    FeatureTool("fillet", "Congé", "Congé", "fillet"),
    FeatureTool("linear_pattern", "Répétition linéaire", "Répétition\nlinéaire", "linear-pattern"),
    FeatureTool("rib", "Nervure", "Nervure", "rib"),
    FeatureTool("draft", "Dépouille", "Dépouille", "draft"),
    FeatureTool("shell", "Coque", "Coque", "shell"),
    FeatureTool("wrap", "Enroulement", "Enroulement", "wrap"),
    FeatureTool("intersect", "Intersection", "Intersection", "intersect"),
    FeatureTool("mirror", "Symétrie", "Symétrie", "mirror"),
    FeatureTool("reference_geometry", "Plan de référence", "Plan", "plane"),
    FeatureTool("curves", "Courbes", "Courbes", "curves"),
    FeatureTool("chamfer", "Chanfrein", "Chanfrein", "chamfer"),
    FeatureTool("circular_pattern", "Répétition circulaire", "Répétition circulaire", "circular-pattern"),
)

FEATURE_GROUPS = (
    ("Volumes", (("extrude",), ("cut_extrude",), ("revolve", "cut_revolve"))),
    ("Parcours", (("sweep", "cut_sweep"), ("loft", "cut_loft"), ("boundary", "cut_boundary"))),
    ("Finitions", (("fillet",), ("chamfer",), ("hole_wizard", "thread"), ("shell", "draft", "rib"))),
    ("Motifs", (("linear_pattern",), ("mirror", "wrap", "intersect"))),
    ("Repères", (("reference_geometry", "curves"),)),
)
