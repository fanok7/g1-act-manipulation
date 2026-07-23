# Inférence ACT sur G1 — setup fonctionnel (2026-07-07)

Statut : **l'inférence tourne** (`eval_g1.py` atteint la boucle de contrôle, `--send_real_robot=false`
testé avec succès). Ce fichier ne garde que ce qui a marché.

## Modèle

```
/home/unitree/unitree_lerobot/unitree_lerobot/models/act_G1_form/checkpoints/100000/pretrained_model/
```
- ACT, `repo_id` dataset associé : `Transfo1919/G1_form` (repo HF rendu public — nécessaire,
  requis par `eval_g1.py` même en pure inférence)
- Robot : `--arm=G1_29`, `--ee=inspire1`
- Une seule caméra tête (`observation.images.cam_high`), pas de caméra poignet

## Commande de lancement (référence)

```bash
source /home/unitree/miniconda3/etc/profile.d/conda.sh
conda activate lerobot
cd /home/unitree/unitree_lerobot

python unitree_lerobot/eval_robot/eval_g1.py \
    --policy.path=unitree_lerobot/models/act_G1_form/checkpoints/100000/pretrained_model \
    --repo_id=Transfo1919/G1_form \
    --frequency=30 \
    --arm="G1_29" \
    --ee="inspire1" \
    --motion=true \
    --visualization=true \
    --send_real_robot=false \
    --rename_map='{"observation.images.cam_left_high": "observation.images.cam_high"}'
```

**`--motion=true` est obligatoire si le robot reste en mode actif** (équilibre embarqué allumé) :
sans ça, le contrôleur bas-niveau (`rt/lowcmd`) entre en conflit avec le contrôleur d'équilibre
du robot → tremblements. `--motion=true` bascule sur `rt/arm_sdk`, prévu pour coexister avec le
mode actif.

**Ne passer `--send_real_robot=true` que robot en position stable, arrêt d'urgence à portée.**
Le verrouillage des jambes/taille (`kp=300`) se déclenche dès le lancement du script, **avant**
le prompt `'s'` de confirmation — c'est du comportement Unitree natif (`robot_arm.py`), pas
modifiable sans toucher au SDK.

## Caméra tête — LE serveur image doit tourner (piège majeur, 2026-07-08)

Symptôme : « l'inférence ne donne presque rien », le robot tient sa pose. Cause : la policy ACT
recevait une **image noire** → elle retombe sur un a priori statique. Diagnostic via un print
`|delta|max` dans la boucle de `eval_g1.py` (delta action−état minuscule et figé = pas de signal
visuel).

Trois causes cumulées, toutes corrigées :

1. **`image_server.py` n'était jamais lancé.** C'est un **4ᵉ process obligatoire** (en plus des 2
   ponts Inspire + `eval_g1`). Il tourne **sur cette machine** (elle EST `192.168.123.164`, l'IP
   que `image_client.py` interroge en ZMQ port 5555). Sans lui : client bloqué sur
   `waiting to receive data...`, `cam_high` = noir.

2. **Mauvais nœud vidéo.** La tête est une **Intel RealSense** dont les `/dev/video*` exposent
   plusieurs flux : `video0`=profondeur (Z16), `video2`=IR, **`video4`=couleur (YUYV)**. L'ancienne
   config lisait `id 0` = profondeur. Corrigé en `head_camera_id_numbers: [4]`.

3. **Mauvaise résolution.** Ancienne config `[480, 1280]` = binoculaire/stéréo, alors que c'est
   **une seule** RealSense couleur **640×480** (= exactement le shape `(3,480,640)` du modèle).
   Corrigé en `head_camera_image_shape: [480, 640]` → `is_binocular=False`.
   Utilisée en mode **opencv/webcam**, PAS via le SDK RealSense (profondeur/IR inutilisées).

Patches associés :
- `image_server.py` : `import pyrealsense2` mis en `try/except` (non installé dans l'env `lerobot`,
  sinon crash à l'import même en mode opencv) ; `__main__` config mono video4, caméra poignet retirée.
- `make_robot.py` : branche réelle passée de `[480,1280]` à `[480,640]` (doit matcher le serveur).

Le `--rename_map cam_left_high→cam_high` reste **nécessaire** : même en mono, la clé publiée est
toujours `observation.images.cam_left_high` (= image entière quand `is_binocular=False`).

Lancement serveur :
```bash
conda activate lerobot
cd /home/unitree/unitree_lerobot
python unitree_lerobot/eval_robot/image_server/image_server.py
# attendu : "Head camera 4 resolution: 480.0 x 640.0" + "waiting for client connections..."
```
Confirmé fonctionnel 2026-07-08 : avec la vraie image, l'inférence marche « beaucoup mieux ».

## Main Inspire1 — chaîne de communication réelle

Sur ce robot, la main Inspire1 communique en **Modbus/Ethernet** (IP internes type
`192.168.123.21x`), **pas** en série UART. Le service officiel `dfx_inspire_service`
(`~/dfx_inspire_service/build/inspire_g1`, protocole série `/dev/ttyTHS0`/`ttyTHS4`) tourne sans
erreur mais ne reçoit jamais rien — confirmé via `./hand_example` (renvoie `0 0 0 0 0 0`).
Ne pas perdre de temps dessus, ce n'est pas le bon driver pour ce matériel.

**Chaîne qui fonctionne, 4 process en parallèle (4 terminaux — inclut le serveur image, cf.
section caméra ci-dessus) :**

1. **Pont Modbus → DDS** (SDK officiel `inspire_hand_ws`, déjà connecté avec succès en Modbus) :
   ```bash
   cd ~/inspire_hand_ws
   source venv/bin/activate   # env à valider/réparer si besoin (voir note ci-dessous)
   python3.8 inspire_hand_sdk/example/Headless_driver_double.py
   ```
   Publie sur `rt/inspire_hand/state/{l,r}` (type `inspire_hand_state`, échelle brute 0-1000).

2. **Pont DDS → DDS** (écrit cette session, traduit vers le format attendu par `unitree_lerobot`) :
   ```bash
   source /home/unitree/miniconda3/etc/profile.d/conda.sh
   conda activate lerobot
   python /home/unitree/unitree_lerobot/tools/inspire_modbus_dds_bridge.py
   ```
   Relit `rt/inspire_hand/state/{l,r}`, republie sur `rt/inspire/state` (type `MotorStates_`,
   échelle normalisée 0.0-1.0) — c'est ce topic que `Inspire_Controller`
   (`robot_hand_inspire.py`) attend. Fait aussi le sens inverse pour `rt/inspire/cmd` →
   `rt/inspire_hand/ctrl/{l,r}`.
   Ne se connecte jamais directement en Modbus — pur traducteur DDS, aucun risque de double
   connexion au bus Modbus.

3. **`eval_g1.py`** (commande de référence ci-dessus).

Note : l'environnement `~/inspire_hand_ws/venv` n'était pas configuré (Python 3.13 vide, deps
manquantes, `venv_x86.tar.xz` inutilisable sur ce Jetson ARM). Si le README de
`inspire_hand_ws` (`python3.8 ...`) ne fonctionne pas tel quel, les mêmes dépendances
(`pymodbus`, `inspire_sdkpy`, `unitree_sdk2py`) sont déjà installées dans l'env conda `lerobot`
— `Headless_driver_double.py` peut être lancé depuis cet env à la place :
```bash
conda activate lerobot
python /home/unitree/inspire_hand_ws/inspire_hand_sdk/example/Headless_driver_double.py
```

## Environnement conda `lerobot` (Jetson Orin, JetPack 5.1.1, CUDA 11.4, Python 3.10)

Versions figées qui fonctionnent ensemble (ne pas laisser `pip`/`conda` les changer sans
re-vérifier immédiatement après) :

| paquet | version | pourquoi |
|---|---|---|
| torch | 2.0.0 | buildé from source pour Jetson (CUDA sm_87), dernière version buildable simplement sur ce JetPack |
| torchvision | 0.15.2a0+fa99a53 | buildé from source, aligné sur torch 2.0.0 |
| numpy | 1.26.4 | torch 2.0.0 compilé contre l'ABI numpy 1.x |
| pandas | 2.1.4 | numpy<2 requis |
| pyarrow | 14.0.2 | idem, versions récentes (24.x) cassent avec numpy 1.x |
| pinocchio | 3.9.0 (conda-forge) | build conda-forge, dont la propre numpy embarquée doit être utilisée (voir piège ci-dessous) |
| casadi | 3.7.2 | IK des bras |
| pymodbus | 3.13.1 | pont main Inspire1 |

Wheels torch/torchvision buildés, à réutiliser si jamais réinstallation nécessaire :
```
/home/unitree/pytorch-build/dist/torch-2.0.0-cp310-cp310-linux_aarch64.whl
/home/unitree/torchvision-build/dist/torchvision-0.15.2a0+fa99a53-cp310-cp310-linux_aarch64.whl
```

### Piège numpy/pinocchio (le plus retors)

`conda install pinocchio -c conda-forge` tire sa propre build numpy (ABI-compatible avec
pinocchio, mais **différente** du wheel pip `numpy==1.26.4` habituel — même version affichée,
binaires différents). Mélanger les deux (installer pinocchio puis forcer numpy via `pip`) casse
`pinocchio.RobotWrapper.buildReducedRobot()` avec une erreur Boost.Python cryptique
(`ArgumentError: ... did not match C++ signature`).

**La bonne procédure, dans cet ordre**, si à refaire :
```bash
conda install -y -n lerobot pinocchio "numpy<2" -c conda-forge
```
en une seule commande conda (laisser conda résoudre les deux ensemble), **jamais** un
`pip install numpy==...` après un `conda install pinocchio` séparé.

### Autres pièges (toujours valables)

- **`pip install -e .` de `lerobot` (submodule) écrase torch/torchvision Jetson par un wheel CPU
  générique.** Réinstaller derrière avec `pip install --force-reinstall --no-deps <wheels ci-dessus>`.
- Toujours `python -m pip`, jamais juste `pip` (PATH peut résoudre vers `~/.local/bin/pip`,
  hors env conda).
- `torch/utils/cpp_extension.py` de cet env est patché pour reconnaître l'arch CUDA `8.7`
  (Orin) — nécessaire pour compiler des extensions CUDA dans cet env.

## Patches de code appliqués (submodule `lerobot` et `unitree_lerobot`)

Tous en clair dans le repo (`git diff`), résumé :

1. **`lerobot/src/lerobot/policies/__init__.py`** — import de `GrootConfig` dans un
   `try/except Exception` (groot tire `diffusers`, qui nécessite `torch.float8_e4m3fn`, absent
   de torch 2.0.0). Pas besoin de groot pour l'inférence ACT.

2. **`lerobot/src/lerobot/datasets/lerobot_dataset.py`** (`load_metadata`) — le fichier
   `meta/episodes/*.parquet` du dataset `Transfo1919/G1_form` sur HF Hub est corrompu (magic
   bytes PAR1 manquants en footer). `load_episodes()` est maintenant dans un `try/except`,
   tolère l'échec (`self.episodes = None`) au lieu de crasher.

3. **`unitree_lerobot/eval_robot/eval_g1.py`** :
   - N'utilise plus `LeRobotDataset` (chargeait aussi `data/*.parquet`, **également corrompu**
     sur ce repo) mais `LeRobotDatasetMetadata` (stats + tâche seulement, pas les frames).
   - Pose initiale du bras : plus tirée du dataset (inaccessible), lue directement depuis la
     position actuelle du robot via `process_images_and_observations`.
   - `task` passé explicitement à `predict_action` (`dataset_meta.tasks.index[0]`, ex.
     `"pick up cube."`) au lieu de `step["task"]`.

4. **`unitree_lerobot/eval_robot/make_robot.py`** — restauré au commit `5c0de80` (avant
   `9980dfc "update image_server version"` qui a cassé la compatibilité avec `eval_g1.py` sans
   mettre à jour ce dernier — bug amont dans le repo `unitree_lerobot`). Caméra poignet
   désactivée (`wrist_camera_type` retiré de `img_config`, ce robot n'en a pas) — avec le fix du
   coup annexe : `wrist_img_array/shape/shm = None` dans la branche sans poignet (sinon
   `NameError` au `return`).

5. **`unitree_lerobot/eval_robot/robot_control/robot_hand_inspire.py`** — timeout de 5s ajouté
   sur l'attente `rt/inspire/state` (bloquait indéfiniment sinon, sans ce topic alimenté).

6. **`unitree_lerobot/eval_robot/utils/rerun_visualizer.py`** — `rr.Scalar(...)` →
   `rr.Scalars([...])` (API renommée dans rerun 0.26.2).

7. **`/home/unitree/unitree_sdk2_python`** — modifications locales non commitées qui
   supprimaient `_MotorCmds_.py`/`_MotorStates_.py` (types IDL requis) restaurées via
   `git stash` (stash conservé : `"backup broken IDL edits before restoring
   MotorCmds_/MotorStates_"` — à examiner si quelqu'un avait une raison de faire ça).

8. **`/home/unitree/inspire_hand_ws/inspire_hand_sdk/inspire_sdkpy/__init__.py`** — import de
   `qt_tabs` (GUI Qt) rendu optionnel (`try/except`), pas nécessaire en usage headless.

9. **Nouveau fichier `unitree_lerobot/tools/inspire_modbus_dds_bridge.py`** — pont DDS↔DDS
   entre les topics du SDK Modbus (`rt/inspire_hand/state|ctrl/{l,r}`) et ceux attendus par
   `unitree_lerobot` (`rt/inspire/state`, `rt/inspire/cmd`). Voir section main Inspire1 ci-dessus.

## Checkpoint — fichiers gardés

Seul le checkpoint `100000/` est conservé (les autres : `020000`-`080000`, supprimés) ; dans
`100000/`, seul `pretrained_model/` est gardé (`training_state/` supprimé, inutile hors reprise
d'entraînement). 198M au lieu de 2.9G.
