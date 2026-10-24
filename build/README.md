# Construire CADastrophe pour Windows

Le paquet conserve Python 3.12, PySide6, pythonocc-core et leurs DLL natives.
Le petit lanceur `CADastrophe.exe` est compilé en C# ; le code Python reste
disponible, notamment pour charger les plugins locaux et lancer les workers
d'échange dans un véritable interpréteur. Aucun Conda ni Python préinstallé
n'est requis sur la machine cible.

## Prérequis et commande

Construire sur Windows x64 avec Conda, .NET Framework 4.x (compilateur `csc.exe`)
et [Inno Setup 6](https://jrsoftware.org/isinfo.php). Créer un environnement
de construction distinct ; `conda-pack` est un outil de livraison et non une
dépendance de l'application :

```powershell
conda env create -f build/environment.yml
conda activate cadastrophe-build
.\build\build.ps1 -RuntimePath $env:CONDA_PREFIX
```

La version par défaut est définie dans [version.iss](version.iss), actuellement
`0.2.0`. `-Version 0.2.1` permet de la remplacer pour une construction.
Le script PowerShell transmet cette même version au compilateur Inno Setup.

Depuis ce dépôt, le runtime natif local `.tools/mamba/envs/.occt-env` est aussi
détecté quand aucun environnement Conda n'est activé. L'outil `conda-pack` peut
être installé dans l'environnement Python de développement `.venv` ; le script
le détecte séparément, sans modifier le runtime natif livré.
`-PackPythonPath 'C:\...\python.exe'` permet de choisir explicitement cet
interpréteur d'empaquetage. Il doit contenir `conda-pack` et `setuptools`.

L'empaquetage conserve les informations de relocalisation de Conda. Si plusieurs
paquets déclarent le même chemin Windows, il retient le contenu réellement
installé et refuse une collision dont le contenu ou la relocalisation reste
ambigu. L'archive est ensuite contrôlée pour exclure les chemins dupliqués.

Pour compiler depuis l'interface Inno Setup, préparer d'abord le paquet avec
`build.ps1 -SkipInstaller -RuntimePath $env:CONDA_PREFIX`, puis ouvrir
`installer.iss` et cliquer sur Compiler. Le fichier charge automatiquement
`AppVersion` depuis `version.iss` et utilise `build/out/<version>/app/` comme
source. Si le paquet a été préparé avec `-Version`, reporter cette version dans
`version.iss` avant une compilation directe, ou laisser `build.ps1` compiler
l'installeur. Un paquet absent produit un message explicite.

`-IsccPath 'C:\...\ISCC.exe'` permet de préciser le compilateur Inno.
`-SkipInstaller` prépare uniquement le dossier applicatif. Le script vérifie
Python et le chargement des bibliothèques natives avant l'empaquetage. Il refuse une sortie déjà
présente ; il ne supprime ni le runtime source ni une ancienne livraison.
En cas d'échec, déplacer la sortie incomplète avant de relancer.

`build/out/<version>/` contient `runtime.zip`, `app/` et l'installeur
`CADastrophe-<version>-windows-x64-setup.exe`. Le dossier `app/` ne doit pas
être lancé avant la compilation de l'installeur : son runtime est relocalisé
au premier lancement. Pour tester le paquet, copier **tout** `app/` dans un
autre dossier puis lancer la copie. Une fois initialisé, ce runtime ne doit
plus être déplacé ; repartir du paquet original pour changer de dossier.
Cette contrainte vient de [conda-pack](https://conda.github.io/conda-pack/).

## Installation par utilisateur

L'installeur utilise `PrivilegesRequired=lowest`, installe par défaut dans
`%LOCALAPPDATA%\Programs\CADastrophe` et crée les raccourcis de cet utilisateur.
Il ne modifie pas le PATH système et n'enregistre pas d'association de fichier.
La [directive Inno](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm)
évite la demande d'élévation ; choisir un autre dossier doit rester compatible
avec les droits du compte courant. Les préférences, récupérations et journaux
restent dans leurs emplacements utilisateur habituels.

Inno Setup 6 limite la longueur des chemins Windows. La construction calcule
la longueur maximale du dossier d'installation à partir des fichiers livrés ;
l'installeur refuse un dossier trop long avant toute copie, avec un message
invitant à choisir un chemin plus court. Les espaces et accents sont acceptés.

Les plugins se trouvent dans `app/plugins/` puis dans le dossier d'installation.
Les exemples, la documentation de développement, les outils et suites de tests
restent dans le dépôt. Le paquet exclut également les en-têtes C/C++, bibliothèques
statiques, caches, tests des dépendances et outils Python de développement.
Le runtime est filtré avant sa relocalisation ; ses DLL, ressources, modules
d'exécution et notices de licence sont conservés. Le code Python de l'application
et des plugins est nécessaire à leur exécution et reste inclus.
Les paquets Python tiers livrés sont limités à OCC, PySide6, shiboken6, NumPy,
svgwrite et leurs petits modules de compatibilité. Les bibliothèques Python
optionnelles et outils présents dans l'environnement de développement ne sont
pas copiés. Les modules internes importés au démarrage par ces dépendances
restent nécessaires ; leurs suites de tests et lanceurs externes sont exclus.
Le fichier local `plugins/settings.json` est exclu du paquet et préservé lors d'une mise à jour.
Les sources des plugins intégrés sont remplacées par la version livrée. Les
extensions ajoutées sous d'autres noms restent présentes. La désinstallation
ne supprime pas les projets, préférences ou extensions ajoutées par l'utilisateur.

Le démarrage ouvre une pièce vierge avec ses trois plans de référence affichés.
Les plans affichent leurs contours ; leur sélection laisse priorité à la pièce
et aux esquisses lorsqu'elles se superposent à l'écran.
La scène de démonstration et sa commande de réinitialisation ne sont pas livrées.
Une mise à jour remplace le runtime privé de l'application et retire les anciens
fichiers de développement livrés, sans supprimer les extensions ajoutées sous
d'autres noms. La construction ne lance pas la suite de tests ; les contrôles
du paquet sont dans `tests/verify_release_package.py` et ne sont pas distribués.

## Qualification d'une livraison

Avant livraison, exécuter la suite native depuis les sources (`main.py
--test-parallel`). Sur une machine Windows sans Conda, sous un compte standard,
installer, démarrer, ouvrir un exemple `.cada`, modifier une cote, recalculer,
annuler/rétablir, sauvegarder et rouvrir. Vérifier les plugins, l'import STEP,
les exports, un chemin avec espaces/accents, la mise à jour puis la
désinstallation. Le lanceur conserve les erreurs dans
`%LOCALAPPDATA%\CADastrophe\logs\launcher.log`.

Le paquet inclut `release.json` (versions exactes, builds, provenance et licences
déclarées des paquets Conda), les métadonnées du runtime et les notices trouvées
dans le cache Conda sous `third-party/`. Compléter les notices si le cache a été
purgé ; vérifier les conditions de redistribution des composants avant publication.
Les versions de l'environnement restent des plages : archiver `release.json`
avec chaque livraison. Signature du binaire et publication sont des opérations
de livraison distinctes, non réalisées par ces scripts.

Avant publication publique du binaire, préparer également les sources exactes
des dépendances soumises à redistribution, avec les recettes et correctifs de
leurs builds. Le runtime actuel inclut une variante GPL de FFmpeg ainsi que
x264/x265 ; les notices et l'inventaire seuls ne constituent pas ces sources.
Consulter les conditions de [FFmpeg](https://ffmpeg.org/legal.html) et de
[Qt](https://www.qt.io/development/open-source-lgpl-obligations). La licence
choisie pour CADastrophe doit être explicite dans le dépôt. Publier les sources
du projet correspondant effectivement au build, y compris les nouveaux fichiers
encore non suivis par Git avant la création du tag de livraison.
