# Manifestival

*Explore and download image files from a IIIF Manifest.*

Une petite application pour **parcourir un manifest IIIF** (le document qui
décrit un manuscrit, un livre ou un recueil numérisé dans une bibliothèque
numérique), **choisir des images** et **les télécharger** avec des noms de
fichiers parlants : cote du manuscrit, numéro d'ordre, folio ou page…

Interface en français, anglais, allemand, italien, tchèque, russe et japonais
(menu **Langue**).

## Ce que fait l'application

- **Ouvrir un document** : coller l'adresse d'un manifest IIIF (versions 2 et 3)
  et cliquer sur **Afficher**. On peut aussi ouvrir un manifest enregistré sur
  l'ordinateur, faire glisser un lien ou un fichier dans la fenêtre, ou
  choisir un document dans une *collection* IIIF. Les adresses déjà ouvertes
  sont proposées dans la liste.
- **Voir les images en vignettes**, avec leurs métadonnées : celles du document
  (onglet **Document** : titre, cote, date, droits…) et celles de l'image
  cliquée (onglet **Image** : libellé, position, dimensions…).
- **Zoomer dans une image jusqu'à sa pleine résolution** : un double-clic (ou
  Entrée) ouvre l'aperçu ; la molette zoome autour du pointeur, on se déplace
  en faisant glisser l'image, un double-clic zoome à cet endroit ; boutons
  *Ajuster* (touche 0) et *100 %* (touche 1, un pixel de l'image par pixel de
  l'écran), jusqu'à 400 %. Comme dans les visionneuses IIIF, seules les
  tuiles de la zone visible sont demandées au serveur, au niveau de détail
  utile ; un serveur sans tuiles envoie l'image entière. *Précédente* /
  *Suivante* (← →) passent d'une image à l'autre.
- **Sélectionner les images** par des cases à cocher :
  - un clic coche ou décoche une image ;
  - pour une suite d'images : un clic sur la première, puis **Maj+clic** (ou
    **Ctrl+Maj+clic**) sur la dernière ;
  - **Tout sélectionner** (Ctrl+A), **Tout désélectionner**, **Inverser la
    sélection** ;
  - au clavier : flèches, Espace pour cocher, Maj+Espace pour une plage.
- **Télécharger** la sélection (bouton en bas à droite) :
  - choix du **dossier**, avec un sous-dossier au nom du préfixe si on le
    souhaite ;
  - **nom des fichiers** composé à la carte : un **préfixe** (l'application
    propose la cote trouvée dans les métadonnées), le **numéro d'ordre**
    (001, 002…), le **libellé de l'image** (f. 1r, p. 12…), un **champ de
    métadonnées** des images ; séparateur au choix (`_`, `-`, `.`, espace),
    entouré d'espaces si on le souhaite (` - `) ; espaces des libellés
    remplacés par le séparateur et accents retirés, si on le souhaite ;
    **aperçu** des noms obtenus ;
  - pour les utilisateurs avancés, un **modèle personnalisé** :
    `{prefix}`, `{n}`, `{label}`, `{title}`, `{meta:Nom du champ}` ;
  - **taille** : la plus grande disponible, ou 3000, 1500, 800 pixels au plus ;
  - si un fichier existe déjà : le garder (pour **reprendre** un téléchargement
    interrompu), le remplacer, ou garder les deux ;
  - en option, la **liste des images** (CSV, lisible par un tableur) et une
    copie du **manifest** (JSON) à côté des images ;
  - progression, arrêt, **nouvel essai** des images en échec, ouverture du
    dossier à la fin.

Les images sont téléchargées une à une, en respectant les serveurs qui
demandent de patienter (code 429, « Retry-After »). Une image n'apparaît sous
son nom définitif qu'une fois complète.

Exemple : avec le préfixe « BnF lat. 1234 », le numéro d'ordre et le libellé,
la 7ᵉ image d'un manuscrit de 250 images, intitulée « f. 4r », devient
`BnF lat. 1234_007_f. 4r.jpg` ; avec le tiret entouré d'espaces,
`BnF lat. 1234 - 007 - f. 4r.jpg` ; avec les espaces remplacés,
`BnF_lat_1234_007_f_4r.jpg`.

## Gallica et les serveurs protégés

Certaines bibliothèques protègent leurs serveurs contre les robots
(Cloudflare pour Gallica) et limitent le rythme des requêtes. Manifestival
s'identifie honnêtement (`Manifestival/version (+adresse du projet)`), sans
se faire passer pour un navigateur, et respecte ces limites :

- pour Gallica, 5 images en pleine taille par minute au plus, comme l'annonce
  la BnF ; la fenêtre de téléchargement l'indique et estime la durée ; moins
  de vignettes et de tuiles demandées à la fois ;
- si une protection refuse une requête (code 403 de Cloudflare), Manifestival
  cesse aussitôt de solliciter le serveur et l'explique, au lieu d'enchaîner
  les refus, ce qui aggraverait le blocage.

Un tel refus dépend aussi du réseau (adresses de centres de données, VPN,
certains réseaux d'établissement). S'il persiste : réessayer plus tard ou
depuis un autre réseau ; enregistrer le manifest depuis le navigateur et
l'ouvrir avec *Ouvrir un fichier…* ; ou écrire à la bibliothèque (pour
Gallica : gallica@bnf.fr).

## Installation

Il faut **Python 3.10 ou plus récent** (https://www.python.org) et la
bibliothèque **PySide6**.

- **Windows** : double-cliquer une fois sur `installer_dependances.bat`, puis
  lancer l'application avec `lancer.bat`.
- **macOS** : dans le Terminal, `python3 -m pip install -r requirements.txt`,
  puis double-cliquer sur `lancer.command`.
- **Linux** : `python3 -m pip install -r requirements.txt`, puis `./lancer.sh`.

En ligne de commande, on peut ouvrir directement un manifest :

```bash
python lancer_manifestival.py https://exemple.org/iiif/manuscrit/manifest.json
```

ou, de façon équivalente, `python -m manifestival https://…`.

## Depuis Spyder

1. Installer PySide6 dans l'environnement Python qu'utilise la console de
   Spyder. Dans cette console : `%pip install PySide6`.
2. Ouvrir `lancer_manifestival.py` et l'exécuter (**F5**).

Manifestival s'ouvre dans sa propre fenêtre, sans bloquer la console. Ses
messages d'erreur s'affichent dans la console, et on peut relancer autant de
fois qu'on veut, par exemple après avoir modifié le code. Il n'y a rien à
régler dans Spyder : l'application tourne dans un processus à part et n'entre
donc pas en conflit avec la bibliothèque Qt de Spyder ni avec ses graphiques.

En haut de `lancer_manifestival.py`, deux réglages :

- `MANIFEST = "https://…"` : un manifest à ouvrir dès le démarrage, pratique
  pendant le développement ;
- `SEPARATE_PROCESS = False` : faire tourner l'application dans la console
  elle-même, pour la suivre au **débogueur** de Spyder (points d'arrêt,
  Ctrl+F5). Il faut alors régler *Outils › Préférences › Console IPython ›
  Graphiques › Backend* sur **Intégré** (Inline) et redémarrer la console.
  Si une autre bibliothèque Qt (PyQt5…) est déjà chargée dans la console,
  Manifestival repasse de lui-même dans un processus à part et le signale.

## Pour les développeurs

```
lancer_manifestival.py      lancement (ligne de commande, Spyder)
manifestival/
  app.py, launch.py         démarrage ; console IPython (Spyder, Jupyter)
  manifest.py               lecture des manifests IIIF 2 et 3 (sans Qt)
  images.py                 adresses des vignettes et des images (API Image)
  tiles.py                  tuiles IIIF du zoom (info.json, niveaux, adresses)
  naming.py                 noms des fichiers (modèles, nettoyage, doublons)
  download.py               téléchargement, reprise, liste CSV (sans Qt)
  net.py, sources.py        réseau et lecture des manifests
  politeness.py             rythme des requêtes selon les serveurs (Gallica…)
  i18n.py, i18n/*.json      traductions (pluriels selon chaque langue)
  ui/                       interface PySide6
tests/                      tests (pytest, pytest-qt) avec un serveur IIIF local
```

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest -q
```

Sous Linux sans écran, lancer les tests avec `QT_QPA_PLATFORM=offscreen`.

Pour ajouter un texte à l'interface : ajouter la clé dans **les sept** fichiers
`manifestival/i18n/*.json` (les tests le vérifient). Un texte qui dépend
d'un nombre est un objet `{"one": …, "other": …}` (tchèque : `few` en plus ;
russe : `few` et `many` ; japonais : `other` seulement).
