"""Sketch workshop: session, drawing, constraints, reshaping and patterns."""
PLACEHOLDERS = (
    ("dimension", "Cotes au clic", "dimension"),
    ("spline", "Spline", "spline"), ("ellipse", "Ellipse", "ellipse"),
    ("slot", "Rainure", "slot"), ("polygon", "Polygone", "polygon"),
    ("text", "Texte d’esquisse", "text"), ("fillet", "Congé d’esquisse", "fillet"),
    ("chamfer", "Chanfrein d’esquisse", "chamfer"), ("point", "Point", "point"),
    ("trim", "Rogner le tracé", "trim"), ("convert", "Projeter la géométrie", "convert"),
    ("offset", "Décaler le tracé", "offset"), ("mirror", "Symétriser les entités", "mirror"),
    ("linear_pattern", "Répétition linéaire", "linear-pattern"),
    ("circular_pattern", "Répétition circulaire", "circular-pattern"),
    ("move", "Déplacer les entités…", "select"),
    ("relations", "Contraintes d’esquisse", "relations"),
    ("repair", "Vérifier l’esquisse", "properties"),
)

GROUPS = (
    ("Session", (("new",), ("edit", "finish", "reattach_support"))),
    ("Tracer", (("line", "rectangle", "circle"), ("arc", "spline", "ellipse"),
                ("slot", "polygon", "point"), ("text", "select"))),
    ("Définir", (("dimension",), ("relations",), ("snap", "repair"))),
    ("Finitions", (("fillet", "chamfer"), ("trim", "offset"), ("convert", "move"))),
    ("Motifs", (("mirror", "linear_pattern", "circular_pattern"),)),
)
