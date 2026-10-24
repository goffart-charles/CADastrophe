# Manifeste, chargement et cycle de vie

Revenir au [guide des plugins](README.md).

## Structure

```text
plugins/
  mon_extension/
    plugin.json
    plugin.py
    outils.py
```

Le dossier du plugin n'a pas besoin d'`__init__.py`. L'hôte le charge comme un
paquet dans un espace de noms isolé ; importer les modules voisins avec
`from .outils import ...`. Ne pas modifier `sys.path` et ne pas importer un autre
plugin par son nom de dossier : déclarer une dépendance puis utiliser ses services.
La découverte trie les dossiers et refuse les identifiants dupliqués.

## Champs de plugin.json

| Champ | Contrat |
|---|---|
| `id` | Identifiant unique en minuscules, lettres/chiffres/`_`, segments séparés par `.` ou `-`. Début : lettre. Exemple : `atelier.controle`. |
| `name` | Texte non vide affiché dans le gestionnaire. |
| `version` | Trois entiers sans zéro initial superflu : `1.2.0`. Aucun suffixe de préversion. |
| `api_version` | Entier `1`. Une autre version rend le plugin incompatible. |
| `entry_point` | `module:fabrique`, un module directement dans le dossier ; la fabrique sans argument retourne l'instance. |
| `enabled_by_default` | Booléen facultatif, `false` par défaut. La configuration utilisateur prime. |
| `dependencies` | Liste facultative de `{"id": "cadastrophe.sketch", "min_version": "1.0.0"}`. |

Les champs inconnus, clés JSON dupliquées et dépendances répétées sont refusés.
Les dépendances se chargent avant leur consommateur ; cycles, versions
insuffisantes, dépendances absentes ou explicitement désactivées bloquent
l'activation. La version du plugin est distincte de la version de l'API et de
celle de ses codecs persistants.

## activate et deactivate

`activate(context)` déclare les contributions. `deactivate()` libère les
ressources propres au plugin et doit être utilisable même après une activation
partielle. Conserver le contexte seulement pendant cette vie active.
L'hôte retire les contributions dans l'ordre inverse, déconnecte les signaux
déclarés par `context.connect`, annule les outils du propriétaire et retire
les modules chargés. Une activation échouée annule aussi ses contributions et
les dépendances activées pour cette tentative.

Ne pas démarrer de traitement ni créer de widget lors d'un simple import de
module. Libérer explicitement les processus, temporisateurs et fichiers que
l'hôte ne possède pas. Une exception d'action ou de prédicat est signalée et
désactive cette action pour la session ; elle n'est pas ignorée silencieusement.

Le gestionnaire protège les documents qui utilisent les types d'une extension
contre son déchargement. Ne pas supposer qu'une désactivation est toujours
possible. Le fichier `plugins/settings.json` appartient à l'utilisateur ; ne
pas le distribuer avec le plugin et ne pas l'écrire directement.
