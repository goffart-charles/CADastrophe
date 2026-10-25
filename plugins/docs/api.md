# Actions, services et sessions

Revenir au [guide](README.md). Les signatures de référence sont dans
[plugin_api.py](../../cadastrophe/plugin_api.py) ; les protocoles interactifs
sont dans [tool_sessions.py](../../cadastrophe/core/tool_sessions.py).

## Contributions de PluginContext

Les clés sont locales au plugin, valides comme identifiants, uniques par type
de contribution. L'hôte préfixe les identifiants par `context.owner`.

| Méthode | Usage |
|---|---|
| `register_action(key, label, callback, *, shortcuts=(), mutates=False, requires_document=True, enabled_when=None, icon=None, from_sketch=False)` | Retourne la QAction du registre. Le prédicat reçoit `ApplicationServices`. `callback=None` déclare une action indisponible. |
| `register_ribbon(key, tab, title, action_keys, *, columns=None)` | Réutilise les actions déjà déclarées. `columns` contient des listes de clés locales, une par colonne. |
| `bind_action_slot(slot, action_key)` | Remplace une position existante de l'hôte ; retourne `False` si elle n'existe pas. Prévoir alors un groupe de ruban. |
| `register_panel(key, title, factory)` | `factory(services, parent)` retourne un QWidget enfant ; `show_panel(key)` l'affiche. |
| `register_status(key)` | Retourne un QLabel permanent, distinct des messages transitoires. |
| `register_service(key, provider)` | Publie `owner.key`, récupérable via `services.service(name)`. |
| `connect(signal, slot)` | Connexion possédée et déconnectée au déchargement. |
| `register_type(codec)` | Déclare un type persistant, voir [persistance](persistance.md). |
| `register_presentation(object_class, factory, *, label, icon, edit_action=None)` | Présentation dérivée d'un type enregistré par ce plugin. `edit_action` est une clé locale déjà déclarée. |
| `register_document_restorer(key, callback)` | Reconstruit les caches après chargement complet des recettes et ressources. Peut retourner un itérateur d'étapes. |
| `start_tool(tool)` / `start_edit(editor, *, new_object=None)` | Démarre une session exclusive possédée par l'hôte. |
| `start_tool_from_sketch(factory)` | Termine l'éditeur d'esquisse valide avant de construire l'outil consommateur. |
| `finish_edit()` / `edit_session` | Termine ou consulte uniquement l'éditeur de ce plugin. |
| `reserve_document()` | Réservation pour une tâche asynchrone ; la fermer dans tous les cas. |
| `refresh()` | Réévalue les disponibilités des actions. |

`icon` désigne un nom de l'[iconothèque existante](../../cadastrophe/ui/icons/),
sans extension. Cette API n'accepte pas un chemin d'icône arbitraire.
Les actions sont accessibles au registre/palette ; déclarer leur présence
dans le ruban explicitement. Ne pas créer une seconde QAction pour la même commande.

Pour une mutation immédiate, `mutates=True` enveloppe le callback dans une
transaction du document. Le callback appelle les méthodes du document ou pousse
ses commandes ; ne pas modifier ses dictionnaires privés. Une action qui ouvre
un outil doit garder `mutates=False` : la session assure sa transaction lors de
la validation. Une transition `from_sketch=True` exige `requires_document=True`
et `mutates=False`.

## ApplicationServices

| Propriété ou méthode | Contrat |
|---|---|
| `active_document`, `documents` | Document courant ou `None`, instantané des documents ouverts. Ne pas conserver une ancienne sélection lors d'un changement de document. |
| `selected_ids`, `selection_items` | UUID sélectionnés et sous-formes natives de la révision courante ; ne pas conserver une sous-forme après recalcul. |
| `select_objects(object_ids)` | Sélectionne des objets entiers par l’autorité commune de l’arbre et de la vue native. Refusé pendant une opération exclusive. |
| `report_problem(text)` | Émet le message et signale un problème visible avec la couleur d’erreur du thème. |
| `editable`, `busy`, `editing`, `edit_owner` | Disponibilité et propriétaire de l'édition courante. |
| `can_start_tool`, `can_start_from_sketch` | Disponibilité pour le lancement d'une session. |
| `display_units` | Conversion d'affichage ; l'unité géométrique interne reste le mm. |
| `clipboard` | Transport MIME vérifié, commun à l'application. |
| `file_dialogs` | Sélecteurs natifs et mémorisation des emplacements. |
| `service(name)` | Recherche d'un service déclaré ; lève `LookupError` s'il est absent. |
| `run_computation(steps, title, **callbacks)` | Calcul par étapes sur les tours Qt, avec annulation. |
| `computation_available`, `computation_busy` | État du service de calcul. |

Signaux : `active_document_changed(document)`, `state_changed()`,
`selection_changed(items)`, `message(str)` et `problem(str)`. Connecter les observateurs avec
`context.connect`, notamment pour mettre à jour un panneau et ses actions.

Services intégrés : `cadastrophe.sketch.profiles` pour les profils,
`cadastrophe.modeling.extrusions` pour la construction et son évaluateur,
`cadastrophe.modeling.history` pour l'historique de corps. Les interfaces
concrètes sont dans [profile_service.py](../sketch/profile_service.py),
[extrusion_service.py](../modeling/extrusion_service.py) et
[body_history.py](../../cadastrophe/core/body_history.py). Déclarer le plugin
producteur comme dépendance. Ne pas atteindre ses attributs privés depuis une
extension tierce ; proposer un service public si une capacité manque.

`Material(nom, density_kg_m3)` décrit une masse volumique positive en kg/m³.
`SceneObject.material` est optionnel et réservé aux corps ;
`Document.set_materials({uuid: material_ou_None})` applique une affectation annulable
sans changer la géométrie. Les volumes internes sont en mm³ et les masses en g.

`DecimalSpinBox`, `EquationSpinBox` et `LengthSpinBox` acceptent le point ou la
virgule comme séparateur décimal, sans séparateur de milliers. Les expressions
acceptent également les deux séparateurs pour les nombres. `LengthSpinBox`
conserve les valeurs internes en mm. `ErrorLabel` et `set_feedback(label, text,
severity="error")` rendent les problèmes visibles dans les thèmes clair et sombre ;
les autres niveaux sont `warning` et `info`.

`VariableSource`, `bind_library(document, path)`, `export_variables(document, path)`
et `reload_variables(document)` sont exposés pour les bibliothèques partagées.
Le rechargement valide tout le graphe et les résultats avant publication dans
une commande annulable. `Document.variable_source` conserve le lien ;
`Document.update_construction(variable_source=None)` le retire en gardant les valeurs.
Le fichier contient les variables et leurs formules, indépendantes des cotes
propres à une pièce. L'ouverture d'un projet utilise ses données enregistrées ;
elle n'applique jamais implicitement un fichier externe modifié.

`Configuration`, `ConfigurationState` et `ParameterSetting` décrivent les variantes
du document. Utiliser `create_configuration(document, nom, source_id=None)`
(copie des substitutions si une source est fournie), `rename_configuration`,
`remove_configuration`, `activate_configuration`, `set_configuration_override`
et `validate_configurations` depuis `cadastrophe.plugin_api`. La cible d’une
substitution est l’UUID d’une variable ou `UUID_objet:clé_dimension` ; `:enabled`
attend un booléen. Une valeur `None` retire la substitution. L’activation exige
un service de recalcul et publie seulement après validation de la candidate.
`Document.active_configuration` et `configuration_state` sont en lecture seule.
Les modifications numériques ordinaires concernent la configuration active ;
`update_construction(scope="common")` modifie les valeurs communes en conservant
les substitutions. Les ajouts et suppressions structurels restent communs.

`activate_configuration`, `remove_configuration`, `set_configuration_override`
et `validate_configurations` acceptent aussi `incremental=True`. Ils retournent
alors un itérateur qui réserve le document dès sa création. Le transmettre à
`services.run_computation` ou le fermer explicitement en cas d'abandon, même
avant sa première étape. L'annulation conserve les paramètres, la configuration
active et l'historique ; seule une candidate entièrement vérifiée est publiée.

`PresentationSurface.navigating` indique un geste de caméra pendant lequel un
éditeur peut suspendre les recherches sous le pointeur.

`CurveObject` et `CurveParameters` décrivent les courbes de construction.
`GeometryReference('curve', uuid)` désigne leur producteur entier, sans indice
d'arête. Leur résultat natif est reconstruit depuis la recette ; voir le
[plugin Courbes](../curves/). L'origine d'une esquisse est le point fixe
`Reference(sketch.id, 'position')`, sans entité ajoutée au profil.

## Outil avec aperçu

Un `ToolSession` fournit `title`, `create_editor(parent, changed)`, `preview()`,
`commit(document, preview)` et `dispose()`. L'éditeur appelle `changed()` à
chaque modification de paramètres. `preview()` valide les entrées et retourne
un `SceneObject` détaché, ou `None` si les paramètres sont incomplets ; une erreur
explicite empêche la validation. L'hôte recalcule un aperçu frais au clic Valider.

Un outil coûteux peut aussi fournir `preview_steps()` et une propriété
`cooperative_preview` vraie. L'itérateur émet `(courant, total, libellé)` et
retourne son `SceneObject` final. L'hôte le consomme sur les tours Qt, le ferme
si les paramètres changent et refuse tout résultat d'une ancienne révision.
La validation attend le résultat courant sans consommer toutes les étapes
dans le callback du clic. `preview()` reste requis pour l'API synchrone ; les
deux chemins doivent employer les mêmes calculs et contrôles natifs.

`commit` utilise les commandes du document, dans la transaction réservée de
l'hôte. Une erreur annule l'ensemble ; Échap annule l'aperçu sans mutation.
`dispose` libère les ressources externes au widget. Un objet d'aperçu ne doit
pas devenir un état métier dans une présentation AIS. Pour un exemple complet,
consulter [primitives/parameters.py](../primitives/parameters.py).

Un `EditSession` persistant fournit aussi `object_id`, `workplane`,
`is_valid(document)` et `refresh(document)`. Les gestes validés passent par les
commandes ordinaires avec undo/redo ; terminer l'éditeur ne regroupe pas toute
la session dans un historique parallèle.

## Calcul asynchrone

Capturer le document avant de lancer un worker. Utiliser
`reservation = context.reserve_document()`, puis
`with reservation.transaction("Importer") as document:` pour la publication
atomique. Toujours appeler `reservation.close()` après succès, erreur ou
annulation. L'hôte ferme aussi les réservations au déchargement.
Qt et les présentations restent sur le thread principal. Les bibliothèques
géométriques ayant des options globales peuvent nécessiter un processus isolé,
comme les [échanges](../import_export/plugin.py). Un calcul long doit offrir
progression, annulation et nettoyage ; ne pas publier un résultat partiel.
