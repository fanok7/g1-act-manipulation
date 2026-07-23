# G1 Republic — politiques de manipulation ACT sur Unitree G1

Apprentissage par imitation sur un **Unitree G1 EDU** (29 DoF, mains Inspire RH52E2) : téléopération
en réalité virtuelle pour collecter les démonstrations, entraînement de politiques **ACT**, puis
inférence embarquée temps réel sur le Jetson Orin du robot.

Deux tâches sont opérationnelles : saisir une bouteille, et manipuler une feuille.

<!-- Vidéo de démonstration : glisser le fichier dans une issue GitHub pour obtenir une URL,
     puis remplacer la ligne ci-dessous.
     https://github.com/user-attachments/assets/xxxxxxxx -->

https://github.com/G1Republic/REPO/assets/VIDEO

---

## Modèles et jeux de données

| | Modèle | Jeu de données | Épisodes |
|---|---|---|---|
| 🍾 Bouteille | [`G1Republic/act_G1_Bottle`](https://huggingface.co/G1Republic/act_G1_Bottle) | [`Transfo1919/G1_Bottle`](https://huggingface.co/datasets/Transfo1919/G1_Bottle) | 75 |
| 📄 Feuille | [`G1Republic/act_G1_form`](https://huggingface.co/G1Republic/act_G1_form) | [`Transfo1919/G1_form`](https://huggingface.co/datasets/Transfo1919/G1_form) | 60 |

Démonstrations enregistrées à 30 fps, une caméra tête 480×640, espace état/action de **26
dimensions** (14 articulations de bras + 6 doigts par main).

Ce dépôt ne contient **que les fichiers de configuration** des modèles ; les poids sont sur le Hub.

```bash
hf download G1Republic/act_G1_Bottle \
    --local-dir unitree_lerobot/models/act_G1_Bottle/pretrained_model
```

## Performances embarquées

Mesuré sur le Jetson Orin du robot (JetPack 5.1, torch 2.0, CUDA 11.4), boucle de contrôle à 30 Hz :

| Politique | Inférence | Cadence réelle | Boucle bloquée |
|---|---|---|---|
| **ACT** | **148 ms** | **27,4 Hz** | 8 % |
| SmolVLA (~450 M) | 913 ms | ~11 Hz | 66 % |

ACT tourne à la fréquence d'enregistrement des démonstrations (30 fps), donc le geste est rejoué à
sa vitesse naturelle. SmolVLA a été porté sur ce matériel et fonctionne, mais son temps d'inférence
bloque la boucle de contrôle : **ACT est le choix retenu pour le temps réel sur ce robot.**

## Démarrage rapide

Quatre processus en parallèle : serveur d'image, deux ponts DDS pour les mains, et l'inférence.

```bash
conda activate lerobot
cd unitree_lerobot

python unitree_lerobot/eval_robot/eval_g1.py \
    --policy.path=unitree_lerobot/models/act_G1_Bottle/pretrained_model \
    --repo_id=Transfo1919/G1_Bottle \
    --frequency=30 --arm="G1_29" --ee="inspire1" \
    --motion=true --visualization=false
```

**→ [`LANCER_INFERENCE.md`](LANCER_INFERENCE.md)** — commandes complètes des 4 terminaux, pour les
deux modèles.

> ⚠️ **Sécurité.** Le robot bouge dès le lancement, y compris avec `--send_real_robot=false` (ce
> drapeau existe mais n'est jamais lu par `eval_g1.py`). Arrêt d'urgence à portée de main.
> Couper avec **Ctrl-C** : la séquence d'arrêt ouvre les mains puis relâche progressivement les
> bras. Ce que la main tient est lâché.

## Documentation

| Document | Contenu |
|---|---|
| [`PIPELINE_G1.md`](PIPELINE_G1.md) | La chaîne complète : téléopération, collecte, conversion, entraînement, inférence, diagnostic |
| [`LANCER_INFERENCE.md`](LANCER_INFERENCE.md) | Commandes d'inférence prêtes à l'emploi |
| [`SESSION_NOTES_inference_setup.md`](SESSION_NOTES_inference_setup.md) | Journal de mise en route et correctifs |
| [`README_unitree.md`](README_unitree.md) | Documentation d'origine d'Unitree |

## Principaux correctifs apportés

Le portage sur ce robot a demandé plusieurs corrections, documentées dans `PIPELINE_G1.md` :

- **Conversion BGR → RGB** avant la politique. `cv2.imdecode` produit du BGR alors que les vidéos
  d'entraînement sont décodées en RGB : le modèle voyait une bouteille bleue en orange et ne
  reconnaissait plus la scène. C'est le correctif qui a débloqué la qualité du comportement.
- **Séquence d'arrêt propre** sur Ctrl-C : ouverture des mains, puis relâchement progressif des bras
  vers le contrôleur d'équilibre embarqué.
- **Pont Modbus → DDS** pour les mains Inspire, qui communiquent en Modbus/Ethernet sur ce robot et
  non en série comme le suppose le pilote officiel.
- **Caméra tête** basculée sur la UGREEN 2K (`/dev/video6`), import `pyrealsense2` rendu optionnel.

### Un piège à connaître : `n_action_steps`

Les politiques à *chunks* prédisent N actions d'un coup et les exécutent **en boucle ouverte** — ni
la caméra ni l'état des bras ne sont consultés entre-temps. Il est tentant de réduire
`n_action_steps` pour « regarder plus souvent » : c'est contre-productif. Sans lissage temporel
(`temporal_ensemble_coeff: None`), chaque nouveau chunk ne prolonge pas celui en cours, et le geste
repart de zéro au lieu d'aboutir. **Laisser `n_action_steps` égal à `chunk_size`.**

## Licence et attribution

Dérivé de [unitreerobotics/unitree_lerobot](https://github.com/unitreerobotics/unitree_lerobot)
(Apache 2.0), lui-même construit sur [LeRobot](https://github.com/huggingface/lerobot) de Hugging
Face. Les mentions de copyright d'origine sont conservées dans les fichiers modifiés.
Voir [`LICENSE`](LICENSE).
