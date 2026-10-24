# Icônes CADastrophe

Les SVG sont dessinés directement dans les sources du projet. Aucun fichier graphique n'a été extrait de SOLIDWORKS, ni repris d'un catalogue tel que Font Awesome ou Lucide. Les captures consultées pendant la conception servaient de références d'organisation et de sens, sans extraction de ressources.

Le jeu courant emploie une palette pétrole/jade/cuivre, des tracés arrondis sur canevas 24 × 24 et une signature à facettes sur 32 × 32. Certaines icônes génériques conservent leur palette antérieure ; validation et annulation utilisent vert et rouge.

Les fichiers restent vectoriels dans `QIcon`, avec variantes HiDPI/désactivées fournies par Qt. Les commandes et libellés sont associés dans [icons.py](../icons.py) et [features.py](../features.py) ; un nom de fichier n'est pas un identifiant métier. Les chevrons clairs/sombres des champs numériques sont également originaux.

Les commandes indisponibles conservent une icône grisée ; elle ne signifie pas que la fonction est implémentée. Les plugins peuvent posséder leurs SVG originaux dans leurs propres dossiers.

La décision d'identité visuelle est conservée dans la documentation technique locale (`doc-dev/decisions/004-identite-visuelle.md`). Cette provenance documentée ne constitue pas une validation juridique.
