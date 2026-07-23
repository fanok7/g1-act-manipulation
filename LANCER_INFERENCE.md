# Lancer une inférence sur le G1 — aide-mémoire

4 process, 4 terminaux, **dans cet ordre**. Les terminaux 1 à 3 sont identiques pour ACT et SmolVLA.

Préambule commun à tous les terminaux conda :

```bash
source /home/unitree/miniconda3/etc/profile.d/conda.sh
conda activate lerobot
cd /home/unitree/unitree_lerobot
```

---

## Terminal 1 — Pont Modbus → DDS (mains Inspire)

```bash
cd ~/inspire_hand_ws
source venv/bin/activate
python3.8 inspire_hand_sdk/example/Headless_driver_double.py
```

Publie sur `rt/inspire_hand/state/{l,r}`.

## Terminal 2 — Pont DDS → DDS

```bash
source /home/unitree/miniconda3/etc/profile.d/conda.sh
conda activate lerobot
python /home/unitree/unitree_lerobot/tools/inspire_modbus_dds_bridge.py
```

## Terminal 3 — Serveur image (caméra tête UGREEN)

```bash
source /home/unitree/miniconda3/etc/profile.d/conda.sh
conda activate lerobot
cd /home/unitree/unitree_lerobot
python unitree_lerobot/eval_robot/image_server/image_server.py
```

Attendu : `Head camera 6 resolution: 480.0 x 640.0` puis `waiting for client connections...`

Vérifier qu'il ne tourne pas déjà avant de le relancer :

```bash
pgrep -af image_server.py
```

## Terminal 4 — Inférence

### ACT (`act_G1_Bottle`)

```bash
source /home/unitree/miniconda3/etc/profile.d/conda.sh
conda activate lerobot
cd /home/unitree/unitree_lerobot

python unitree_lerobot/eval_robot/eval_g1.py \
    --policy.path=unitree_lerobot/models/act_G1_Bottle/pretrained_model \
    --repo_id=Transfo1919/G1_Bottle \
    --frequency=30 \
    --arm="G1_29" \
    --ee="inspire1" \
    --motion=true \
    --visualization=false \
    --send_real_robot=false \
    --rename_map='{"observation.images.cam_left_high": "observation.images.cam_high"}' \
    2>&1 | tee /tmp/eval_act.log
```

### ACT (`act_G1_form`) — tâche feuille

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
    --visualization=false \
    --send_real_robot=false \
    --rename_map='{"observation.images.cam_left_high": "observation.images.cam_high"}' \
    2>&1 | tee /tmp/eval_form.log
```

Seuls `--policy.path` et `--repo_id` changent par rapport à `act_G1_Bottle`. Le chemin passe par
`checkpoints/100000/` (arborescence différente de Bottle). `chunk_size=100` et
`n_action_steps=100` : chunk exécuté en entier, comme Bottle — ne pas y toucher.

### SmolVLA (`smolvla_bottle`)

```bash
source /home/unitree/miniconda3/etc/profile.d/conda.sh
conda activate lerobot
cd /home/unitree/unitree_lerobot

python unitree_lerobot/eval_robot/eval_g1.py \
    --policy.path=unitree_lerobot/models/smolvla_bottle \
    --repo_id=Transfo1919/G1_Bottle \
    --frequency=30 \
    --arm="G1_29" \
    --ee="inspire1" \
    --motion=true \
    --visualization=false \
    --send_real_robot=false \
    --rename_map='{"observation.images.cam_left_high": "observation.images.camera1"}' \
    2>&1 | tee /tmp/eval_smolvla.log
```

Deux différences avec ACT : `--rename_map` cible **`camera1`** (pas `cam_high`), et le chargement
prend ~15 s de plus (backbone SmolVLM2).

---

## Différences entre les deux modèles

| | ACT Bottle | ACT form | SmolVLA |
|---|---|---|---|
| `--policy.path` | `models/act_G1_Bottle/pretrained_model` | `models/act_G1_form/checkpoints/100000/pretrained_model` | `models/smolvla_bottle` |
| `--repo_id` | `Transfo1919/G1_Bottle` | `Transfo1919/G1_form` | `Transfo1919/G1_Bottle` |
| clé `--rename_map` | `cam_high` | `cam_high` | `camera1` |
| `chunk_size` | 50 | 100 | 50 |
| `n_action_steps` | 50 | 100 | 50 (remettre depuis `.bak50`) |
| forward mesuré | **148 ms** | non mesuré | **913 ms** |
| cadence réelle à 30 Hz | **27,4 Hz** | non mesurée | ~11 Hz (prédit) |

`--arm`, `--ee`, `--motion`, `--frequency` sont identiques partout.

**`n_action_steps` doit rester égal à `chunk_size`** (chunk exécuté en entier). Le baisser dégrade
nettement le geste : sans lissage temporel (`temporal_ensemble_coeff: None` sur les trois modèles),
chaque nouveau chunk ne prolonge pas celui en cours → discontinuités, le geste « repart de zéro ».
Constaté sur ACT Bottle le 2026-07-23 (15 nettement moins bon que 50).

---

## Réglage fluidité / réactivité (SmolVLA)

Se règle dans `unitree_lerobot/models/smolvla_bottle/config.json`, clé **`n_action_steps`**
(valeur actuelle : **15**). **Ne pas toucher à `chunk_size`** (50) ni `n_obs_steps` (1) : figés à
l'entraînement.

Le modèle prédit 50 actions d'un coup et en exécute `n_action_steps` avant de reregarder la caméra.
Pendant l'exécution d'un chunk, il est en **boucle ouverte** : il ignore la caméra ET l'état des bras.

| `n_action_steps` | plus réactif | plus fluide |
|---|---|---|
| 15 | ✅ regarde souvent | ❌ figé 66 % du temps |
| 25 | | |
| 50 (défaut d'origine) | ❌ 1,7 s aveugle | ✅ figé 36 % du temps |

Sauvegarde de la valeur 50 : `config.json.bak50`.

```bash
cd /home/unitree/unitree_lerobot/unitree_lerobot/models/smolvla_bottle
cp config.json.bak50 config.json     # revenir à n_action_steps=50
```

⚠️ **Ne jamais restaurer `config.json.orig`** : c'est la config d'avant le patch `[6]→[26]`,
elle recasserait le modèle.

---

## Pièges à connaître

- **`--send_real_robot=false` ne protège pas.** Le flag n'est jamais lu par `eval_g1.py` : le
  contrôleur de bras est instancié quoi qu'il arrive, les articulations se verrouillent et les
  commandes DDS partent. **Arrêt d'urgence à portée dans tous les cas.**
- **`--motion=true` est obligatoire** si l'équilibre embarqué est actif, sinon conflit `rt/lowcmd`
  → tremblements.
- **Le verrouillage jambes/taille se déclenche avant le prompt `'s'`** (comportement Unitree natif).
- **`--visualization=false`** : à `true`, rerun échoue en gRPC et la boucle tombe à 2,4 Hz au lieu
  de ~11 Hz (constaté 2026-07-22 sur SmolVLA ; cause à confirmer).
- **Couper avec Ctrl-C**, pas `kill` : la séquence d'arrêt (ouverture des mains puis relâchement
  progressif des bras) n'est déclenchée que par Ctrl-C ou une fin normale.

## Séquence d'arrêt (Ctrl-C)

1. **Ouverture des mains** — les doigts Inspire sont renvoyés à `q = 1.0` (ouvert), 0,5 s d'attente.
   ⚠️ Ce qui est tenu est **lâché** : ne pas couper au-dessus du vide.
2. **Relâchement des bras** — fade du poids `arm_sdk` 1 → 0 sur 2 s (en `--motion=true`), le
   contrôleur d'équilibre reprend la main. Jambes et taille restent verrouillées.

---

## Vérifier que tout tourne

```bash
# les 4 process
pgrep -af "image_server.py|Headless_driver_double|inspire_modbus_dds_bridge|eval_g1.py"

# l'image réellement reçue par la policy (pendant l'inférence)
ls -l /dev/shm/psm_*          # doit faire 921600 octets = 480*640*3
```

Diagnostic complet (à lancer pendant l'inférence) :

```bash
python /tmp/claude-1000/-home-unitree/223a64be-0b33-48c9-909a-943d787ea450/scratchpad/diag.py
```

Mesurer la cadence réelle depuis un log :

```bash
grep -oP '\d\d:\d\d:\d\d\.\d+ INFO\s+\[\d+\]' /tmp/eval_smolvla.log | tail -20
```

---

## Voir la caméra en direct

Sans perturber l'inférence (s'abonne au flux ZMQ, ne touche pas à `/dev/video6`) :

```bash
python /tmp/claude-1000/-home-unitree/223a64be-0b33-48c9-909a-943d787ea450/scratchpad/cam_view.py
# puis ouvrir http://192.168.123.164:8090/
```

---

## Notes détaillées

- ACT : `SESSION_NOTES_inference_setup.md`
- SmolVLA : `SESSION_NOTES_smolvla_inference.md`
