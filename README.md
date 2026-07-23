# Politiques de manipulation ACT sur Unitree G1

Apprentissage par imitation sur un Unitree G1 EDU (29 DoF, mains Inspire RH52E2) : téléopération en réalité virtuelle pour collecter les démonstrations, entraînement de politiques ACT, puis inférence embarquée temps réel sur le Jetson Orin du robot.
Tâche opérationnelle : saisir une bouteillee.

https://github.com/user-attachments/assets/a73e3916-3887-46c8-945c-4247a61ed420

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

ACT tourne à la fréquence d'enregistrement des démonstrations (30 fps), donc le geste est rejoué à
sa vitesse naturelle.

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

## Licence et attribution

Dérivé de [unitreerobotics/unitree_lerobot](https://github.com/unitreerobotics/unitree_lerobot)
(Apache 2.0), lui-même construit sur [LeRobot](https://github.com/huggingface/lerobot) de Hugging
Face. Les mentions de copyright d'origine sont conservées dans les fichiers modifiés.
Voir [`LICENSE`](LICENSE).
