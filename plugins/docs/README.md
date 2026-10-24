# Créer un plugin CADastrophe

CADastrophe charge des extensions Python locales avec une API publique de
version **1**, exposée par [cadastrophe.plugin_api](../../cadastrophe/plugin_api.py).
Ce guide décrit les contrats réellement utilisés par l'hôte.

- [Premier plugin et manifeste](demarrage.md)
- [Actions, services et outils interactifs](api.md)
- [Objets persistants et fonctions paramétriques](persistance.md)
- [Tests et distribution](validation.md)

Les plugins s'exécutent dans le processus de l'application avec les droits du
compte courant. Installer uniquement du code de confiance. Ils utilisent le
même Python 3.12 et les mêmes bibliothèques natives que l'hôte.

## Exemple minimal complet

Créer `plugins/mon_extension/` avec les deux fichiers ci-dessous. Les dossiers
sont découverts au démarrage, uniquement au premier niveau de `plugins/`.
Le gestionnaire d'extensions est accessible par **Extensions → Gestion des
extensions** ; les changements de configuration prennent effet au prochain
démarrage.

`plugin.json` :

```json
{
  "id": "exemple.informations",
  "name": "Informations de la pièce",
  "version": "1.0.0",
  "api_version": 1,
  "entry_point": "plugin:create_plugin",
  "enabled_by_default": true
}
```

`plugin.py` :

```python
from cadastrophe.plugin_api import PluginContext


class Informations:
    def activate(self, context: PluginContext):
        self.context = context
        context.register_action(
            "compter", "Compter les objets", self.compter,
            icon="properties", enabled_when=lambda services: services.editable,
        )
        context.register_ribbon("outils", "Affichage", "Informations", ("compter",))

    def compter(self):
        services = self.context.services
        document = services.active_document
        services.message.emit(f"{document.display_name} : {len(document.objects)} objets")

    def deactivate(self):
        self.context = None


def create_plugin():
    return Informations()
```

L'action a pour identifiant `exemple.informations.compter`. Elle est intégrée
au registre de commandes et au ruban, et désactivée quand le document est
indisponible. Aucun attribut privé de `MainWindow` n'est utilisé. Cet exemple
affiche une information ; il ne crée pas de fonction paramétrique.

Pour aller plus loin, lire les plugins intégrés :
[primitives](../primitives/plugin.py) pour les outils,
[mesures](../measurements/plugin.py) pour une action de lecture,
[esquisse](../sketch/plugin.py) pour un éditeur persistant,
[modélisation](../modeling/plugin.py) pour les fonctions et le recalcul,
[échanges](../import_export/plugin.py) pour les tâches asynchrones.
