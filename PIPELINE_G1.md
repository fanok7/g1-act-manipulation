# Pipeline G1 — téléopérer, entraîner, inférer

Guide de reprise pour ce robot précis (Unitree G1 EDU 29 DoF, mains Inspire, Jetson Orin JetPack 5.1).

Le `README.md` de ce dépôt est celui d'Unitree, générique. **Ce fichier-ci décrit ce qui est
spécifique à notre installation** : ce qui a été modifié, ce qui casse, et pourquoi.

État au 2026-07-23 : chaîne complète fonctionnelle, trois modèles entraînés et inférables
(`act_G1_Bottle`, `act_G1_form`, `smolvla_bottle`).

---

## 0. Vue d'ensemble

```
   PC Windows 11 + WSL2                    Robot G1 (Jetson)
   ┌──────────────────────┐                ┌──────────────────────────┐
   │ Meta Quest (USB/adb) │                │ image_server.py (caméra) │
   │        ↓             │   Ethernet     │ ponts Inspire (mains)    │
   │ xr_teleoperate  ─────┼───192.168.123──┤ eval_g1.py (inférence)   │
   │        ↓             │      DDS       └──────────────────────────┘
   │ dataset JSON         │
   │        ↓             │
   │ conversion LeRobot   │
   │        ↓             │
   │ entraînement (GPU PC)│  ── scp du modèle ──▶  models/<nom>/
   └──────────────────────┘
```

**Le robot ne fait que l'inférence.** La téléopération et l'entraînement se font depuis le PC
Windows/WSL2 — le Jetson n'a ni la puissance ni un environnement assez stable pour entraîner.

Adresses : PC = `192.168.123.200`, robot = `192.168.123.164` (Ethernet direct, Wi-Fi à part pour
internet).

---

## 1. Téléopération et collecte de données

Documentation détaillée : `~/TELEOP_G1.md` (côté robot).

Client `xr_teleoperate` (env conda `tv`) dans **WSL2 Ubuntu 22.04**, casque **Meta Quest en USB**
via `adb reverse` (pas en Wi-Fi). Mains Inspire **RH52E2 variante FTP** → `--ee=inspire_ftp`
(surtout pas `dfx`).

### Checklist après chaque reboot

1. IP statique côté Windows (PowerShell admin) :
   `New-NetIPAddress -InterfaceAlias "Ethernet" -IPAddress 192.168.123.200 -PrefixLength 24`
2. **Désactiver le pare-feu Windows** :
   `Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False`
3. `conda activate tv`
4. Retrouver l'interface réseau **par sa MAC** (le nom change à chaque reboot en mode mirrored) :
   `ip a | grep -B1 "04:7c:16"` → passer `--network-interface=<ethX>`
5. `ping 192.168.123.164`
6. Sur le robot : `image_server.py`
7. Sur le robot : `Headless_driver_double.py` (en `python3.8`)
8. Dans WSL : `teleop_hand_and_arm.py --network-interface=ethX`
9. Attendre `🟢 Press [r]`
10. **Ensuite seulement** : `adb start-server && adb devices && adb reverse tcp:8012 tcp:8012`
11. Sur le Quest : `https://localhost:8012?ws=wss://localhost:8012` (Advanced → Proceed unsafe)

L'ordre 8→10 est **impératif** : le serveur Vuer doit écouter avant l'`adb reverse`.

### Pièges connus

| Symptôme | Cause / correctif |
|---|---|
| DDS ne reçoit rien | Pare-feu Windows — le désactiver complètement. Le mode unicast CycloneDDS ne suffit pas. |
| Interface introuvable | Le nom `ethX` change à chaque reboot → toujours chercher par MAC `04:7c:16:aa:56:ea` |
| `invalid CPU 5` | `--affinity` inutilisable, WSL n'a que 4 cœurs → ne pas le passer |
| Port 8012 occupé | vieux process (`pkill -9 -f teleop_hand_and_arm.py`), portproxy Windows, ou adb lancé trop tôt (`taskkill /F /IM adb.exe`). **Ne jamais tuer `svchost.exe`** — ça casse le réseau WSL. |
| `ImportError: cannot import name 'Flag'` | `params-proto` doit être **< 3.0** (2.13.2) pour vuer 0.0.60 |
| `inspire_sdkpy` introuvable | pas sur PyPI : cloner `NaCl-1374/inspire_hand_ws` puis `pip install -e inspire_hand_sdk/` |

Enregistrement des épisodes : option `--record` de `teleop_hand_and_arm.py` → dataset JSON brut.

---

## 2. Conversion du dataset

Sur le PC (WSL2), pas sur le robot.

```bash
# 1. tri / renommage des épisodes
python unitree_lerobot/utils/sort_and_rename_folders.py --data_dir $HOME/datasets/<nom>

# 2. JSON brut -> format LeRobot (+ upload HF)
python unitree_lerobot/utils/convert_unitree_json_to_lerobot.py \
    --raw-dir $HOME/datasets/<nom> \
    --repo-id Transfo1919/<nom> \
    --robot_type Unitree_G1_Inspire \
    --push_to_hub
```

`--robot_type Unitree_G1_Inspire` pour nos mains. Le `repo-id` choisi ici est celui qu'on passera
en `--repo_id` à l'inférence : **il doit correspondre**, l'inférence lit les stats de normalisation
du dataset.

Nos datasets : `Transfo1919/G1_Bottle` (75 épisodes), `Transfo1919/G1_form` (60 épisodes), tous
deux en 30 fps, 1 caméra tête `cam_high` 480×640, state/action = **26** (14 bras + 6+6 doigts).

---

## 3. Entraînement

Sur le PC avec GPU, dans `unitree_lerobot/lerobot`.

```bash
python src/lerobot/scripts/lerobot_train.py \
    --dataset.repo_id=Transfo1919/G1_Bottle \
    --policy.type=act \
    --policy.push_to_hub=false
```

Pour SmolVLA, on part du modèle pré-entraîné :

```bash
python src/lerobot/scripts/lerobot_train.py \
    --dataset.repo_id=Transfo1919/G1_Bottle \
    --policy.path=lerobot/smolvla_base \
    --policy.push_to_hub=false
```

> ⚠️ **Piège SmolVLA majeur.** Avec `--policy.path=lerobot/smolvla_base`, les features du dataset
> **ne remplacent pas** celles de la config de base (`factory.py`, `if not cfg.input_features` est
> faux). Le `config.json` produit déclare `observation.state=[6]`, `action=[6]` et `camera1/2/3`
> alors que le modèle a réellement appris **26** dimensions et une seule caméra. Il faut patcher le
> checkpoint à la main (voir §4). Les stats du normaliseur, elles, sont correctes en 26 — c'est ce
> qui prouve que l'entraînement était bon.

### Transfert vers le robot

```bash
scp -r <chemin>/checkpoints/<N>/pretrained_model \
    unitree@192.168.123.164:/home/unitree/unitree_lerobot/unitree_lerobot/models/<nom>/
```

Attention : selon la façon dont le `scp` est fait, l'arborescence diffère. `act_G1_form` a gardé
`checkpoints/100000/pretrained_model/`, `act_G1_Bottle` et `smolvla_bottle` ont les fichiers plus
haut. Vérifier avant de passer `--policy.path`.

---

## 4. Patcher un checkpoint SmolVLA (à faire une fois)

Uniquement pour SmolVLA. Détail complet dans `SESSION_NOTES_smolvla_inference.md`.

1. **Shapes `[6]` → `[26]`** dans `config.json`, `policy_preprocessor.json`,
   `policy_postprocessor.json` (garder des `.orig`).
2. **`transformers==4.51.3`** — installer avec `python -m pip`, jamais `pip` seul : le `pip` du
   robot pointe sur un Python 3.8 hors-env et installe au mauvais endroit.
3. **Shim torch** dans `<env>/lib/python3.10/site-packages/sitecustomize.py` : torch 2.0 est trop
   vieux pour transformers ≥ 4.50. Le shim ajoute `torch.compiler`, les dtypes `float8_*`, et
   émule `load_state_dict(assign=True)`. Sans lui : `Cannot copy out of meta tensor`.
   Il répare aussi l'import d'ACT au passage.
4. **Patch `smolvlm_with_expert.py`** : `low_cpu_mem_usage=False`, retrait de `device_map`,
   `.to(device)` manuel.

---

## 4 bis. Récupérer les poids du modèle

Les poids ne sont **pas dans ce dépôt git** (82 Mo à 865 Mo selon le modèle) : seuls les
`config.json` sont versionnés. Ils vivent sur le Hub Hugging Face.

```bash
hf auth login          # une fois, avec un token write
hf download G1Republic/act_G1_Bottle \
    --local-dir unitree_lerobot/models/act_G1_Bottle/pretrained_model
```

Pour publier un nouveau modèle entraîné :

```bash
hf upload G1Republic/<nom> <chemin>/pretrained_model . --repo-type=model
```

Ne pas pousser `training_state/` (l'état de l'optimiseur, ~164 Mo, inutile en inférence).

## 5. Inférence

**→ Voir `LANCER_INFERENCE.md`** : commandes prêtes à copier pour les trois modèles, ordre des
4 terminaux, vérifications.

Résumé : 4 process en parallèle — pont Modbus→DDS, pont DDS→DDS, serveur image, `eval_g1.py`.

### Performances mesurées (Jetson, 30 Hz demandés)

| modèle | forward | cadence réelle | temps figé |
|---|---|---|---|
| ACT | 148 ms | 27,4 Hz | 8 % |
| SmolVLA | 913 ms | ~11 Hz | 66 % à `n_action_steps=15` |

**ACT est ~6× plus léger et tourne à la vitesse du dataset (30 fps).** SmolVLA ne peut pas
l'atteindre sur ce matériel : son forward bloque la boucle de contrôle. Pour du temps réel sur ce
robot, préférer ACT.

### `n_action_steps` : laisser égal à `chunk_size`

Les policies à chunks prédisent N actions d'un coup et les rejouent **en boucle ouverte** (ni la
caméra ni l'état des bras ne sont consultés pendant l'exécution du chunk).

Tentant de le baisser pour « regarder plus souvent » — **c'est contre-productif**. Sans lissage
temporel (`temporal_ensemble_coeff: None` sur nos trois modèles), un nouveau chunk ne prolonge pas
celui en cours : chaque raccord crée une discontinuité et le geste repart de zéro au lieu d'aller
au bout. Constaté le 2026-07-23 : ACT à 15 nettement moins bon qu'à 50.

`chunk_size` est une dimension du réseau, figée à l'entraînement. `n_action_steps` ne peut jamais
le dépasser.

---

## 6. Correctifs appliqués au code (à ne pas perdre)

| Fichier | Modif | Pourquoi |
|---|---|---|
| `eval_robot/make_robot.py` | `cv2.cvtColor(..., COLOR_BGR2RGB)` dans `process_images_and_observations` | **Bug majeur.** `cv2.imdecode` sort du BGR, les vidéos d'entraînement sont en RGB → le modèle voyait la bouteille bleue en **orange** et ne reconnaissait rien. |
| `eval_robot/eval_g1.py` | `rename_map=cfg.rename_map` dans `make_policy` | sinon `validate_visual_features_consistency` plante sur SmolVLA |
| `eval_robot/eval_g1.py` | `except KeyboardInterrupt` + séquence d'arrêt dans `finally` | les bras restaient verrouillés à l'arrêt |
| `robot_control/robot_arm.py` | `release_arms()` | fade du poids `arm_sdk` 1→0, le contrôleur d'équilibre reprend les bras |
| `robot_control/robot_hand_inspire.py` | `open_hands()` | ouvre les doigts **depuis le processus principal** : le contrôleur des mains est un `Process` enfant que Ctrl-C tue avant qu'il ait pu publier |
| `image_server/image_server.py` | `import pyrealsense2` en try/except, config mono `video6` | pyrealsense2 absent de l'env ; caméra tête = UGREEN |

---

## 7. Pièges de fonctionnement

- **`--send_real_robot=false` ne protège pas.** Le flag existe mais **n'est jamais lu** par
  `eval_g1.py` : le contrôleur de bras est instancié quoi qu'il arrive, les articulations se
  verrouillent, les commandes DDS partent. **Arrêt d'urgence à portée dans tous les cas.**
- **`--motion=true` obligatoire** si l'équilibre embarqué est actif, sinon conflit `rt/lowcmd` →
  tremblements. Bascule sur `rt/arm_sdk`.
- **Le verrouillage jambes/taille se déclenche avant le prompt `'s'`** (comportement Unitree natif).
- **`--visualization=false`** : à `true`, rerun échoue en gRPC et fait tomber la boucle à 2,4 Hz.
- **Couper avec Ctrl-C**, jamais `kill` : la séquence d'arrêt (ouverture des mains, puis
  relâchement des bras) n'est déclenchée que par Ctrl-C ou une fin normale.
  ⚠️ Ce que la main tient est **lâché** — ne pas couper au-dessus du vide.
- **Main Inspire = Modbus/Ethernet, pas série.** Le service officiel `dfx_inspire_service`
  (protocole série) tourne sans erreur mais ne reçoit jamais rien sur ce robot. Ne pas perdre de
  temps dessus : la chaîne qui marche passe par les deux ponts DDS.
- **Env conda `lerobot` fragile** : torch 2.0.0 compilé pour Jetson sm_87. Tout `pip install` ou
  `conda install` d'un paquet tiers (lerobot, pinocchio, casadi) peut écraser torch/torchvision par
  des versions génériques sans CUDA. Après toute installation, vérifier :
  ```bash
  python -c "import torch; print(torch.__version__, torch.cuda.is_available())"  # 2.0.0 True
  ```
  Réparation : wheels locaux dans `~/pytorch-build/dist/` et `~/torchvision-build/dist/`.

---

## 8. Diagnostiquer

**Le test qui tranche pour tout problème « il ne voit rien »** : lire la mémoire partagée que la
policy consomme réellement, pendant que l'inférence tourne.

```bash
ls -l /dev/shm/psm_*     # doit faire 921600 octets = 480*640*3
```

```python
from multiprocessing import shared_memory
import numpy as np, cv2
shm = shared_memory.SharedMemory(name="psm_XXXX")   # nom vu ci-dessus
img = np.ndarray((480, 640, 3), np.uint8, buffer=shm.buf).copy()
cv2.imwrite("vu.png", img)          # tel quel = ce qui entre dans le modèle
print(img.mean())                    # ~100 = OK, ~0 = image noire
```

Comparer à une frame d'entraînement (les vidéos sont en **AV1**, OpenCV ne les décode pas sur
Jetson → passer par ffmpeg) :

```bash
python -c "from huggingface_hub import hf_hub_download; print(hf_hub_download('Transfo1919/G1_Bottle','videos/observation.images.cam_high/chunk-000/file-000.mp4',repo_type='dataset'))"
ffmpeg -ss 10 -i <chemin.mp4> -frames:v 1 train.png
```

C'est comme ça que le bug BGR/RGB a été trouvé.

**Mesurer la cadence réelle** depuis un log lancé avec `| tee` :

```bash
grep -oP '\d\d:\d\d:\d\d\.\d+ INFO\s+\[\d+\]' /tmp/eval_act.log | tail -20
```

Un ralentissement périodique dont la période égale `n_action_steps` = le forward du modèle ; son
surcoût donne directement le temps d'inférence.

**Voir la caméra en direct** sans perturber l'inférence (s'abonne au flux ZMQ, ne touche pas à
`/dev/video6`) : voir `LANCER_INFERENCE.md`.

---

## 9. Fichiers de référence

| Fichier | Contenu |
|---|---|
| `LANCER_INFERENCE.md` | commandes d'inférence prêtes à l'emploi |
| `SESSION_NOTES_inference_setup.md` | mise en route ACT, détail des correctifs env |
| `SESSION_NOTES_smolvla_inference.md` | mise en route SmolVLA, les 5 problèmes résolus, latences |
| `~/TELEOP_G1.md` | téléopération, doc historique |
| `README.md` | doc Unitree générique (amont) |
