# Persistance et fonctions paramétriques

Revenir au [guide](README.md).

## ObjectCodec

Importer `SceneObject`, `ObjectType` et `ObjectCodec` depuis
`cadastrophe.plugin_api`. Un objet spécialisé est une dataclass immuable dérivée
de `SceneObject`. Enregistrer `ObjectCodec(type_id, object_class, version,
encode, decode, ...)` avec `context.register_type(codec)`.

| Champ | Contrat |
|---|---|
| `type_id` | Préfixé par l'identifiant du plugin suivi d'un point ; unique. |
| `object_class` | Sous-classe propre au codec, différente de SceneObject. |
| `version` | Entier positif. Le lecteur accepte uniquement la version courante. |
| `encode(obj)` | Dictionnaire JSON fini : pas de tuple, QObject, AIS, forme native ou cache. |
| `decode(base, data)` | Retourne exactement la classe enregistrée, en conservant tous les champs communs et l'identité de `base.shape`. |
| `clipboard_references(data)` | Liste/tuple de UUID canoniques référencés par la recette. |
| `clipboard_remap(data, id_map)` | Remappe toutes ces références avec la table en lecture seule fournie par l'hôte. |
| `validate_references(obj, objects)` | Validation contre la table complète des objets après décodage du lot. |
| `native_resources` | Tuple de noms de champs de la dataclass contenant des B-Rep sources exactes, stockées séparément du JSON. |

Les deux fonctions de presse-papiers sont optionnelles mais doivent être
déclarées ensemble, même pour un type sans référence qui retourne `()`.
Sans ce contrat, la copie du type est refusée. Vérifier que les références
remappées correspondent exactement à celles du lot et qu'aucun UUID source
n'est réutilisé dans la copie.

L'hôte vérifie qu'encode/decode ne perd aucun champ persistant. La forme finale
d'un solide ordinaire est enregistrée en B-Rep ; celle d'une fonction logique
ou d'un corps paramétrique est reconstruite. Un plugin requis absent empêche
l'ouverture plutôt que de supprimer ses objets.

Le format natif courant est `cadastrophe.document`, schéma **9**, extension
`.cada`. Les schémas livrés 6, 7 et 8 sont migrés explicitement : matériau absent
pour le schéma 6, lien de bibliothèque absent pour 6 et 7, puis configuration
par défaut sans substitution pour les trois versions, sans modifier les UUID,
paramètres ni expressions. Le schéma 9 conserve les configurations, leurs valeurs
communes et substitutions, et l’UUID actif ; les recettes structurelles restent
communes et les résultats sont reconstruits.
Les schémas de développement 1 à 5 et les anciennes versions de codecs ne sont plus lus. Ne pas
renuméroter un codec pour une modification sans changement de son contrat de
données. Un futur changement de schéma doit prévoir explicitement la conversion
des fichiers concernés avant de remplacer le lecteur.

## Une fonction n'est pas un solide figé

Une fonction persistante contient une recette, les UUID des dépendances et les
références topologiques nommées par leur producteur. Son résultat doit pouvoir
être reconstruit après ouverture et après modification amont.
Réutiliser les classes et l'évaluateur de
[feature_model.py](../../cadastrophe/core/feature_model.py),
[feature_objects.py](../../cadastrophe/core/feature_objects.py) et
[body_history.py](../../cadastrophe/core/body_history.py).
L'API de contribution ne permet pas à elle seule de rendre une opération
arbitraire paramétrique : il faut intégrer sa recette, ses dépendances, son
évaluation, son éditeur et sa réparation au service de construction.

Conserver UUID, expressions et état d'activation lors d'une édition. Préparer
une candidate indépendante, vérifier les dépendances, cycles, préconditions et
validité des B-Rep, puis publier en une transaction. Une fonction désactivée,
en erreur ou bloquée garde sa recette ; un ancien résultat valide ne doit pas
servir silencieusement d'entrée à une construction nouvelle.

Les références à une face ou arête désignent le producteur exact et ses noms
persistants. Refuser une référence absente ou ambiguë et proposer sa réparation.
Ne pas choisir une face par indice ou proximité. Conserver les références qui
restent identifiables sans ambiguïté après une modification.
Les motifs transforment les volumes d'effet des fonctions sources, jamais le
solide final ; refuser explicitement les sources incompatibles.

Le perçage guidé expose `HoleFeature` et `HoleParameters` dans l’API publique.
Sa source est une esquisse de points ; `profile_sections` transporte aussi
cette dépendance sans exiger de contour fermé. Le service
`cadastrophe.sketch.profiles.position_points(document, sketch_id, entity_ids=())`
retourne le repère résolu et les couples `(UUID, point monde)`. Une sélection
vide suit tous les points, dont les occurrences associatives. Les volumes
soustractifs restent rejouables et les traversées sont recalculées sur la cible
de chaque occurrence.

Le viewport conserve sa navigation native et le chemin commun de conversion
écran/rayon/repère ; appliquer le facteur DPI une fois. Les saisies et résultats
s'affichent selon `display_units`, sans redimensionner les solides existants.
Les libellés utilisent le vocabulaire métier ; les détails du moteur restent
dans le code et les journaux.
