"""'
Refer to:   lerobot/lerobot/scripts/eval.py
            lerobot/lerobot/scripts/econtrol_robot.py
            lerobot/robot_devices/control_utils.py
"""

import time
import torch
import logging

import numpy as np
from pprint import pformat
from dataclasses import asdict
from torch import nn
from contextlib import nullcontext
from typing import Any
from lerobot.policies.factory import make_policy, make_pre_post_processors
from lerobot.utils.utils import (
    get_safe_torch_device,
    init_logging,
)
from lerobot.configs import parser
from lerobot.datasets.lerobot_dataset import LeRobotDatasetMetadata
from lerobot.policies.pretrained import PreTrainedPolicy
from multiprocessing.sharedctypes import SynchronizedArray
from lerobot.processor.rename_processor import rename_stats
from lerobot.processor import (
    PolicyAction,
    PolicyProcessorPipeline,
)
from unitree_lerobot.eval_robot.make_robot import (
    setup_image_client,
    setup_robot_interface,
    process_images_and_observations,
)
from unitree_lerobot.eval_robot.utils.utils import (
    cleanup_resources,
    predict_action,
    to_list,
    to_scalar,
    EvalRealConfig,
)
from unitree_lerobot.eval_robot.utils.rerun_visualizer import RerunLogger, visualization_data

import logging_mp

logger_mp = logging_mp.getLogger(__name__)
logger_mp.setLevel(logging_mp.INFO)


def eval_policy(
    cfg: EvalRealConfig,
    task: str,
    policy: PreTrainedPolicy | None = None,
    preprocessor: PolicyProcessorPipeline[dict[str, Any], dict[str, Any]] | None = None,
    postprocessor: PolicyProcessorPipeline[PolicyAction, PolicyAction] | None = None,
):
    assert isinstance(policy, nn.Module), "Policy must be a PyTorch nn module."

    logger_mp.info(f"Arguments: {cfg}")

    if cfg.visualization:
        rerun_logger = RerunLogger()

    # Reset policy and processor if they are provided
    if policy is not None and preprocessor is not None and postprocessor is not None:
        policy.reset()
        preprocessor.reset()
        postprocessor.reset()

    image_info = None
    arm_ctrl = None
    ee_ctrl = None
    try:
        # --- Setup Phase ---
        image_info = setup_image_client(cfg)
        robot_interface = setup_robot_interface(cfg)

        # Unpack interfaces for convenience
        arm_ctrl, arm_ik, ee_ctrl, ee_shared_mem, arm_dof, ee_dof = (
            robot_interface[key]
            for key in ["arm_ctrl", "arm_ik", "ee_ctrl", "ee_shared_mem", "arm_dof", "ee_dof"]
        )
        tv_img_array, wrist_img_array, tv_img_shape, wrist_img_shape, is_binocular, has_wrist_cam = (
            image_info[key]
            for key in [
                "tv_img_array",
                "wrist_img_array",
                "tv_img_shape",
                "wrist_img_shape",
                "is_binocular",
                "has_wrist_cam",
            ]
        )

        # Get initial pose from the robot's current arm position.
        # (Normally taken from the first frame of the training dataset, but the
        # actual data parquet files on this dataset's repo are corrupted, so we
        # fall back to wherever the arm currently is instead of a dataset-recorded
        # starting pose.)
        _, init_arm_pose = process_images_and_observations(
            tv_img_array, wrist_img_array, tv_img_shape, wrist_img_shape, is_binocular, has_wrist_cam, arm_ctrl
        )
        init_arm_pose = init_arm_pose[:arm_dof]

        user_input = input("Enter 's' to initialize the robot and start the evaluation: ")
        idx = 0
        print(f"user_input: {user_input}")
        full_state = None
        if user_input.lower() == "s":
            # "The initial positions of the robot's arm and fingers take the initial positions during data recording."
            logger_mp.info("Initializing robot to starting pose...")
            tau = robot_interface["arm_ik"].solve_tau(init_arm_pose)
            robot_interface["arm_ctrl"].ctrl_dual_arm(init_arm_pose, tau)
            time.sleep(1.0)  # Give time for the robot to move
            # --- Run Main Loop ---
            logger_mp.info(f"Starting evaluation loop at {cfg.frequency} Hz.")
            while True:
                loop_start_time = time.perf_counter()
                # 1. Get Observations
                observation, current_arm_q = process_images_and_observations(
                    tv_img_array, wrist_img_array, tv_img_shape, wrist_img_shape, is_binocular, has_wrist_cam, arm_ctrl
                )
                left_ee_state = right_ee_state = np.array([])
                if cfg.ee:
                    with ee_shared_mem["lock"]:
                        full_state = np.array(ee_shared_mem["state"][:])
                        left_ee_state = full_state[:ee_dof]
                        right_ee_state = full_state[ee_dof:]
                state_tensor = torch.from_numpy(
                    np.concatenate((current_arm_q, left_ee_state, right_ee_state), axis=0)
                ).float()
                observation["observation.state"] = state_tensor
                # 2. Get Action from Policy
                action = predict_action(
                    observation,
                    policy,
                    get_safe_torch_device(policy.config.device),
                    preprocessor,
                    postprocessor,
                    policy.config.use_amp,
                    task,
                    use_dataset=cfg.use_dataset,
                    robot_type=None,
                )
                action_np = action.cpu().numpy()
                # 3. Execute Action
                arm_action = action_np[:arm_dof]

                # --- Diagnostic live: état vs action prédite (toutes les 10 iters) ---
                if idx % 10 == 0:
                    delta = arm_action - current_arm_q
                    np.set_printoptions(precision=3, suppress=True, linewidth=200)
                    logger_mp.info(
                        f"[{idx}] |delta|max={np.abs(delta).max():.4f}  "
                        f"state={current_arm_q}  action={arm_action}  delta={delta}"
                    )

                tau = arm_ik.solve_tau(arm_action)
                arm_ctrl.ctrl_dual_arm(arm_action, tau)

                if cfg.ee:
                    ee_action_start_idx = arm_dof
                    left_ee_action = action_np[ee_action_start_idx : ee_action_start_idx + ee_dof]
                    right_ee_action = action_np[ee_action_start_idx + ee_dof : ee_action_start_idx + 2 * ee_dof]
                    # logger_mp.info(f"EE Action: left {left_ee_action}, right {right_ee_action}")

                    if isinstance(ee_shared_mem["left"], SynchronizedArray):
                        ee_shared_mem["left"][:] = to_list(left_ee_action)
                        ee_shared_mem["right"][:] = to_list(right_ee_action)
                    elif hasattr(ee_shared_mem["left"], "value") and hasattr(ee_shared_mem["right"], "value"):
                        ee_shared_mem["left"].value = to_scalar(left_ee_action)
                        ee_shared_mem["right"].value = to_scalar(right_ee_action)

                if cfg.visualization:
                    visualization_data(idx, observation, state_tensor.numpy(), action_np, rerun_logger)
                idx += 1
                # Maintain frequency
                time.sleep(max(0, (1.0 / cfg.frequency) - (time.perf_counter() - loop_start_time)))
    except KeyboardInterrupt:
        logger_mp.info("Interrupted by user.")
    except Exception as e:
        logger_mp.info(f"An error occurred: {e}")
    finally:
        # Open the fingers before the arms go slack, so anything held is set down rather than
        # dropped from wherever the arm happens to be. Published straight from this process:
        # the hand controller's own loop lives in a daemon child that Ctrl-C kills first.
        if ee_ctrl is not None and hasattr(ee_ctrl, "open_hands"):
            try:
                ee_ctrl.open_hands()
            except Exception as e:
                logger_mp.warning(f"Failed to open hands: {e}")

        # Whatever brought us here — Ctrl-C, a crash, a clean exit — don't leave the arms
        # holding the last commanded pose.
        if arm_ctrl is not None:
            try:
                arm_ctrl.release_arms(duration=2.0)
            except Exception as e:
                logger_mp.warning(f"Failed to release arms: {e}")
        if image_info:
            cleanup_resources(image_info)


@parser.wrap()
def eval_main(cfg: EvalRealConfig):
    logging.info(pformat(asdict(cfg)))

    # Check device is available
    device = get_safe_torch_device(cfg.policy.device, log=True)

    torch.backends.cudnn.benchmark = True
    torch.backends.cuda.matmul.allow_tf32 = True

    logging.info("Making policy.")

    dataset_meta = LeRobotDatasetMetadata(repo_id=cfg.repo_id)
    # dataset_meta.tasks is a DataFrame indexed by task description, e.g. "pick up cube."
    task = dataset_meta.tasks.index[0]

    # Pass rename_map so make_policy skips the strict visual-feature check when the
    # dataset's camera keys differ from the policy config (e.g. cam_high -> camera1 for
    # SmolVLA fine-tuned from smolvla_base). Mirrors lerobot_train.py.
    policy = make_policy(cfg=cfg.policy, ds_meta=dataset_meta, rename_map=cfg.rename_map)
    policy.eval()

    preprocessor, postprocessor = make_pre_post_processors(
        policy_cfg=cfg.policy,
        pretrained_path=cfg.policy.pretrained_path,
        dataset_stats=rename_stats(dataset_meta.stats, cfg.rename_map),
        preprocessor_overrides={
            "device_processor": {"device": cfg.policy.device},
            "rename_observations_processor": {"rename_map": cfg.rename_map},
        },
    )

    with torch.no_grad(), torch.autocast(device_type=device.type) if cfg.policy.use_amp else nullcontext():
        eval_policy(cfg, task, policy, preprocessor, postprocessor)

    logging.info("End of eval")


if __name__ == "__main__":
    init_logging()
    eval_main()
