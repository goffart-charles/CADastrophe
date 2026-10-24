# Bride Atlas — démonstration paramétrique

Ouvrir **bride_atlas.cada** dans CADastrophe, avec les extensions Esquisse et
Modélisation activées. Le fichier contient les recettes et les références :
la géométrie est reconstruite à l’ouverture, sans solide importé ni maillage.

La pièce mesure environ **148 × 124 × 38 mm**. Elle comprend un seul solide
connexe, **18 fonctions**, **8 esquisses** et **3 plans personnalisés**.
Les esquisses et les plans sont masqués au départ pour faciliter la lecture
de la pièce ; leurs cases dans l’arbre permettent de les afficher.

## Ce que contient l’arbre

| Fonctions | Construction |
|---|---|
| 01 | Révolution complète du flasque et du moyeu autour d’une ligne de construction |
| 02 | Deuxième révolution additive, formant la couronne supérieure |
| 03 | Révolution soustractive, formant la gorge annulaire |
| 04–06 | Plot extrudé, répétition linéaire de deux plots, puis répétition circulaire du motif : douze plots |
| 07–09 | Trou coté Ø4,2, enlèvement à travers tout, répétition linéaire puis circulaire : douze trous |
| 10–12 | Patte extrudée, symétrie YZ, puis symétrie du motif : quatre pattes |
| 13–15 | Trou de fixation Ø5 et deux symétries : quatre perçages |
| 16 | Évent percé à travers tout depuis un plan incliné de 15° |
| 17–18 | Congé supérieur R0,8 et chanfrein d’entrée de l’alésage de 0,6 mm |

Le **plan A** suit la face supérieure du flasque, au préfixe de la première
révolution. Le **plan B** suit le sommet du premier plot. Le **plan C** est
incliné autour d’une ligne de construction portée par le plan A. Les faces
et arêtes sont référencées par leurs noms topologiques persistants.

## Manipulations à essayer

Faire une modification à la fois, puis utiliser Annuler avant la suivante.

1. Dans l’esquisse **07 · Perçage pilote**, modifier la cote de diamètre de
   **4,2 à 5,4 mm** : les douze trous doivent s’agrandir.
2. Double-cliquer sur la fonction **04 · Plot radial**, passer de **6 à 10 mm** :
   les douze plots grandissent, le plan B et l’esquisse du trou suivent leur
   sommet, et les perçages restent traversants.
3. Dans **06**, passer de **6 à 7** occurrences. Faire la même modification
   dans **09** pour aligner les trous avec les plots : quatorze plots percés.
   Entre les deux validations, les motifs ne sont plus alignés ; modifier les
   deux paramètres avant d’évaluer le résultat final.
4. Dans l’esquisse **01**, déplacer de **Z=8 à Z=12 mm** les deux sommets
   `(62,8)` et `(24,8)` du profil. Le flasque s’épaissit et les plans, plots,
   perçages et finitions suivants se reconstruisent. Conserver les autres
   sommets et les identifiants des segments.
5. Double-cliquer sur **Plan C**, passer de **15 à 22°** : l’évent change
   d’inclinaison en conservant son diamètre et sa condition à travers tout.

**bride_atlas_variante.cada** contient le résultat cumulé de ces cinq
modifications. Son identité documentaire est distincte : les deux fichiers
peuvent être ouverts simultanément dans deux onglets. Les identifiants des
fonctions, esquisses et références sont conservés pour permettre la comparaison.

## Vérification reproductible

Le générateur est `examples/parametric_flange.py`. Le test natif est
`tests/test_parametric_flange_demo.py`. Depuis la racine du dépôt :

```powershell
$env:CADASTROPHE_DEMO_OUTPUT = "$PWD/examples/bride_atlas"
$env:PYTEST_ADDOPTS = '-k "test_atlas_demo_rebuild_roundtrip_and_real_history_editor" --tb=short'
& .venv/Scripts/python.exe main.py --test-parallel
```

Ce scénario complet effectue de nombreuses reconstructions de la même pièce.
Il est lancé explicitement pour ne pas rallonger la suite de tests courante.
Pour le lancer dans un dossier temporaire, définir
`$env:CADASTROPHE_COMPLEX_DEMO_TEST = '1'` sans `CADASTROPHE_DEMO_OUTPUT` :
les exemples livrés ne sont alors pas réécrits.

Le test vérifie chaque préfixe de l’historique, la validité et la connexité du
solide, les positions réelles des trous et de la matière, les variations de
volume attendues après modification, le suivi des supports, les identifiants,
la sauvegarde et la reconstruction à la réouverture. Il ouvre aussi les deux
fichiers avec les extensions réelles de l’application, puis modifie une ancienne
extrusion depuis son panneau et vérifie Annuler.

Les images proviennent du viewport OCCT natif. **validation.json** consigne les
mesures et les vérifications du passage réussi.

## Ouverture et arbre

Les branches de l’arbre sont repliées à l’ouverture. Les branches développées
manuellement restent ouvertes lors d’un rafraîchissement du même document.

Le suivi topologique utilise une indexation OCCT temporaire des correspondances
exactes, valable pour un résultat géométrique donné. Les références sauvegardées
restent des noms persistants ; aucun indice de face ne sert d'identité après
reconstruction.

Le profilage est reproductible séparément avec
`CADASTROPHE_PROFILE_DEMO=1` et le test `test_profile_atlas_native_open` dans
`tests/test_topology_lookup.py`. Les durées dépendent du modèle, du runtime et de
la machine ; mesurer le passage courant plutôt que réutiliser un ancien bilan.
Les commandes de lancement des tests figurent dans le
[README du projet](../../README.md#développement).
