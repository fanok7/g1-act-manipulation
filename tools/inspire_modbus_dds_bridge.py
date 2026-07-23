"""
Bridge between the Modbus/Ethernet-based Inspire hand SDK (inspire_hand_ws,
topics rt/inspire_hand/state|ctrl/{l,r}, type inspire_hand_state/inspire_hand_ctrl)
and the topics unitree_lerobot's eval_g1.py expects (rt/inspire/state, rt/inspire/cmd,
type MotorStates_/MotorCmds_ from unitree_sdk2py).

This process does NOT talk to the hand directly or open any Modbus connection --
it only translates between two sets of DDS topics that are already on the bus.
The actual Modbus<->DDS bridging for rt/inspire_hand/* is done by
inspire_hand_ws/inspire_hand_sdk/example/Headless_driver_double.py, which must
be running separately for this bridge to have any real data to relay.

Joint order (both sides, 0-5 = right hand, 6-11 = left hand):
  pinky, ring, middle, index, thumb-bend, thumb-rotation

Value scale:
  rt/inspire_hand/*   -> angle_act/angle_set: raw int16, 0 (closed) .. 1000 (open)
  rt/inspire/*        -> q: float, 0.0 (closed) .. 1.0 (open)
"""

import time
import threading

from unitree_sdk2py.core.channel import ChannelPublisher, ChannelSubscriber, ChannelFactoryInitialize
from unitree_sdk2py.idl.unitree_go.msg.dds_ import MotorCmds_, MotorStates_
from unitree_sdk2py.idl.default import unitree_go_msg_dds__MotorCmd_, unitree_go_msg_dds__MotorState_

from inspire_sdkpy.inspire_dds import inspire_hand_state, inspire_hand_ctrl
from inspire_sdkpy.inspire_hand_defaut import get_inspire_hand_ctrl

PUBLISH_HZ = 100.0

_lock = threading.Lock()
_right_angle_act = [0] * 6
_left_angle_act = [0] * 6
_have_right = False
_have_left = False


def _on_right_state(msg: inspire_hand_state):
    global _have_right
    with _lock:
        _right_angle_act[:] = msg.angle_act
        _have_right = True


def _on_left_state(msg: inspire_hand_state):
    global _have_left
    with _lock:
        _left_angle_act[:] = msg.angle_act
        _have_left = True


def _on_inspire_cmd(msg: MotorCmds_, ctrl_pub_r: ChannelPublisher, ctrl_pub_l: ChannelPublisher):
    if len(msg.cmds) < 12:
        return
    right_q = [msg.cmds[i].q for i in range(0, 6)]
    left_q = [msg.cmds[i].q for i in range(6, 12)]

    def to_angle_set(q_list):
        return [int(max(0.0, min(1.0, q)) * 1000) for q in q_list]

    cmd_r = get_inspire_hand_ctrl()
    cmd_r.angle_set = to_angle_set(right_q)
    cmd_r.mode = 0b0001  # angle control
    ctrl_pub_r.Write(cmd_r)

    cmd_l = get_inspire_hand_ctrl()
    cmd_l.angle_set = to_angle_set(left_q)
    cmd_l.mode = 0b0001
    ctrl_pub_l.Write(cmd_l)


def main():
    ChannelFactoryInitialize(0)

    # Subscribe to the Modbus-bridged hand state (published by Headless_driver_double.py)
    state_sub_r = ChannelSubscriber("rt/inspire_hand/state/r", inspire_hand_state)
    state_sub_r.Init(_on_right_state, 10)
    state_sub_l = ChannelSubscriber("rt/inspire_hand/state/l", inspire_hand_state)
    state_sub_l.Init(_on_left_state, 10)

    # Publisher for rt/inspire/state, consumed by unitree_lerobot's Inspire_Controller
    inspire_state_pub = ChannelPublisher("rt/inspire/state", MotorStates_)
    inspire_state_pub.Init()

    # Publishers to relay commands back down to the Modbus bridge's ctrl topics
    ctrl_pub_r = ChannelPublisher("rt/inspire_hand/ctrl/r", inspire_hand_ctrl)
    ctrl_pub_r.Init()
    ctrl_pub_l = ChannelPublisher("rt/inspire_hand/ctrl/l", inspire_hand_ctrl)
    ctrl_pub_l.Init()

    # Subscribe to rt/inspire/cmd, published by unitree_lerobot's Inspire_Controller
    inspire_cmd_sub = ChannelSubscriber("rt/inspire/cmd", MotorCmds_)
    inspire_cmd_sub.Init(lambda msg: _on_inspire_cmd(msg, ctrl_pub_r, ctrl_pub_l), 10)

    print("Bridge started. Waiting for rt/inspire_hand/state/{l,r}...")
    waited = 0.0
    while not (_have_right and _have_left):
        time.sleep(0.1)
        waited += 0.1
        if waited > 0 and int(waited * 10) % 10 == 0:
            print(f"  ...still waiting ({waited:.0f}s). Is Headless_driver_double.py running?")

    print("Got state from both hands. Publishing rt/inspire/state.")

    period = 1.0 / PUBLISH_HZ
    while True:
        start = time.time()
        with _lock:
            right = list(_right_angle_act)
            left = list(_left_angle_act)

        states = MotorStates_(states=[unitree_go_msg_dds__MotorState_() for _ in range(12)])
        for i in range(6):
            states.states[i].q = right[i] / 1000.0
        for i in range(6):
            states.states[6 + i].q = left[i] / 1000.0
        inspire_state_pub.Write(states)

        time.sleep(max(0.0, period - (time.time() - start)))


if __name__ == "__main__":
    main()
