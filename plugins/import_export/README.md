# Import STEP et export multi-formats

Extension locale de confiance, identifiant `cadastrophe.import_export`.

**Fichier → Exporter…**, le panneau Échanges ou Ctrl+K donnent accès à STL binaire/ASCII, STEP B-Rep exact, OBJ, 3MF et glTF/GLB. L'import accepte STEP. Les échanges utilisent un processus natif séparé, avec progression, annulation et publication atomique.

`exporters.py` décrit les formats ; `mesh.py` centralise la triangulation et ses contrôles. L'ancienne commande `export_step` reste disponible. Aucun paquet supplémentaire n'est nécessaire aux formats actuels.

Les icônes sont des SVG originaux écrits pour le projet.
