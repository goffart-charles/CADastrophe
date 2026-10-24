# Tester et distribuer un plugin

Revenir au [guide](README.md).

## Validation native

Utiliser l'environnement de [environment.yml](../../environment.yml), avec
Python 3.12, PySide6 et pythonocc-core. Depuis les sources :

```powershell
python main.py --probe
$env:PYTEST_ADDOPTS = '-k "test_mon_extension" --tb=short'
try { python main.py --test-parallel }
finally { Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue }
```

Sous Windows, le lanceur utilise des bureaux privés avec les vraies fenêtres
Qt et la géométrie native. Pour le runtime local du dépôt, remplacer
`python main.py` par `.\run.ps1`. Ne pas valider le viewport par un rendu simulé.

Tester les comportements qui concernent l'extension :

- Chargement, dépendances absentes/cycliques, API incompatible, erreur pendant
  l'activation et nettoyage des contributions. Réactivation sans doublon.
- Disponibilité des actions sans document, pendant un outil et au changement
  de document. Annulation d'aperçu et exception de validation sans mutation.
- Pour une recette : vrai recalcul après modification amont, identités,
  références absentes/ambiguës, B-Rep, erreurs en aval et undo/redo.
- Sauvegarde/réouverture, plugin absent, version non prise en charge et données
  corrompues. Copie avec remappage si le codec la déclare.
- Interface native : sélection, unités, saisie et DPI ; recette humaine distincte
  des tests automatiques.

Les exemples exécutables de tests sont [test_plugins.py](../../tests/test_plugins.py),
[test_plugin_services.py](../../tests/test_plugin_services.py),
[test_plugin_types.py](../../tests/test_plugin_types.py),
[test_tool_sessions.py](../../tests/test_tool_sessions.py) et la
[fixture sample](../../tests/fixtures/plugins/sample/plugin.py).
Placer les fixtures hors du dossier `plugins/` de production.

## Distribution

Distribuer un dossier comprenant `plugin.json`, le point d'entrée et tous les
modules/ressources nécessaires. L'utilisateur le copie au premier niveau de
`plugins/`, à côté des extensions intégrées, puis redémarre l'application et
vérifie son état dans le gestionnaire. Il n'existe pas d'installation de paquet
ou de téléchargement automatique de dépendances depuis le manifeste.

Un plugin emploie la bibliothèque standard et les dépendances natives de
l'application. Toute bibliothèque supplémentaire doit être justifiée et livrée
pour le même Python et la même architecture ; ne pas lancer `pip` depuis
`activate`. Indiquer les versions testées et les licences des ressources.
Préserver la provenance des SVG originaux.

Ne pas distribuer `settings.json`, préférences, récupération, caches, fichiers
de test ou projets utilisateur. Pour inclure une nouvelle extension intégrée
dans la livraison Windows, elle doit se trouver dans `plugins/` avant de lancer
le [build](../../build/README.md). Une extension tierce ne peut pas remplacer
silencieusement l'identifiant d'une extension existante.
