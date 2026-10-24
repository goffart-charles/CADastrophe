# Extensions de CADastrophe

Les extensions locales contiennent chacune un `plugin.json` et leur module Python. **Extensions → Gestion des extensions** mémorise leur activation dans `settings.json` (ignoré par Git), appliquée au prochain démarrage.

| Dossier | Rôle |
|---|---|
| `primitives/` | Création de boîte, cylindre, sphère et cône comme solides ordinaires. |
| `sketch/` | Esquisses, contraintes, profils et présentation native. |
| `modeling/` | Fonctions et reconstruction paramétriques ; dépend d'Esquisse. |
| `curves/` | Hélices, spirales et courbes 3D par points ; dépend de Modélisation. |
| `measurements/` | Mesures géométriques, volumes, masses et affectation de matériaux aux corps. |
| `import_export/` | Import STEP et exports exacts/maillés. |

Les contrats publics sont exposés par [cadastrophe.plugin_api](../cadastrophe/plugin_api.py). Le plugin de validation reste dans [tests/fixtures/plugins/sample/](../tests/fixtures/plugins/sample/), hors découverte normale.

Le [guide de création des plugins](docs/README.md) présente un exemple complet,
le manifeste, l'API, les sessions interactives, la persistance, les tests et la distribution.
