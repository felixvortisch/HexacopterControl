#!/usr/bin/env python3

import time
import threading
import copy
import numpy as np

from simrocketenv import SimRocketEnv
from mpcpolicy import MPCPolicy


# Global messagebox to exchange data between threads
g_thread_msgbox = {
    'ctrl_fps' : 0,      # information: publish fps of control thread
    # u
}
g_thread_msgbox_lock = threading.Lock() # access guard for g_thread_msgbox
g_sim_running = True # Run application as long as this is set to True

def ctrl_thread_func(initial_state):
    """
      The control algorithm (control policy) runs in a separate
      thread here so that the simulation and the controller
      can run at a fixed rate.
      This thread consumes the current state vector (observation)
      and emits a control input (u) / action vector.
      The data is exchanged over the global mailbox g_thread_msgbox.
    """
    global g_thread_msgbox
    global g_thread_msgbox_lock
    global g_sim_running

    # Switch between policies here:
    # -----------------------------

    policy = MPCPolicy(initial_state)
    #policy = NNPolicy()
    # policy = PPOPolicy()

    print("Active policy: %s" % (policy.get_name()))

    # make sure a control algorithm update is performed in the first epoch
    CTRL_DT_SEC = 1.0 / 100.0  # run the control law every XX ms
    timestamp_last_ctrl_update = time.time() - 2*CTRL_DT_SEC
    ctrl_fps_counter = 0  # +1 for every step, reset every 1 sec
    timestamp_last_mpc_fps_update = time.time()

    while g_sim_running:
        timestamp_current = time.time()
        if timestamp_current - timestamp_last_ctrl_update < CTRL_DT_SEC:
            time.sleep(0)
            continue

        with g_thread_msgbox_lock:
            state = copy.deepcopy(g_thread_msgbox['state'])
            if timestamp_current - timestamp_last_mpc_fps_update >= 1.0:
                g_thread_msgbox['ctrl_fps'] = ctrl_fps_counter
                ctrl_fps_counter = 0
                timestamp_last_mpc_fps_update = timestamp_current

        u, predictedX = policy.next(state)

        timestamp_last_ctrl_update = timestamp_current
        ctrl_fps_counter += 1
        with g_thread_msgbox_lock:
            g_thread_msgbox['u'] = np.copy(u) # emit control vector u

def main():
    """
      Entry point for the real-time simulation with GUI
    """
    global g_thread_msgbox
    global g_thread_msgbox_lock
    global g_sim_running

    # SimRocketEnv is handling the physics simulation
    env = SimRocketEnv(interactive=True)
    g_thread_msgbox['state'] = env.state # publish state vector
    u = None # control input / action

    # Spawn NMPC thread (doing the control work)
    nmpc_thread = threading.Thread(
            target=ctrl_thread_func,
            kwargs={'initial_state': g_thread_msgbox['state']})
    nmpc_thread.start()

    timestamp_lastupdate = time.time()
    MAX_DT_SEC = 0.1 # don't allow larger simulation timesteps than this
    SIM_DT_SEC = 1.0 / 120.0  # run the simulation every XX ms
    sim_step_counter = 0  # +1 for every simulation step, reset every 1 sec
    # emit a FPS stat message every second based on this timestamp:
    last_fps_update = timestamp_lastupdate
    reward_sum = 0.0

    # main loop
    while g_sim_running:
        timestamp_current = time.time()
        dt_sec = timestamp_current - timestamp_lastupdate
        if dt_sec < SIM_DT_SEC:
            time.sleep(0) # run the simulation in real-time at fixed rate
            continue

        # get control vector u from NMPC thread:
        with g_thread_msgbox_lock:
            if 'u' in g_thread_msgbox:
                u = copy.deepcopy(g_thread_msgbox['u']) # get control input
            else:
                # control thread is not ready. wait with the simulation thread
                # until we receive the first u vector e.g. the MPC thread needs
                # a bit of startup time to compile .c files until it is ready
                continue

        timestamp_lastupdate = timestamp_current
        # make sure dt_sec is within a reasonable range:
        if dt_sec > MAX_DT_SEC:
            print("Warning, high dt_sec: %.1f" % (dt_sec))
            continue
        dt_sec = np.clip(dt_sec, 0.0, MAX_DT_SEC)

        env.dt_sec = dt_sec # dynamic dt_sec, set before the call to step()
        state, reward, done, _, _ = env.step(u) # update physics simulation
        sim_step_counter += 1
        reward_sum += reward

        with g_thread_msgbox_lock:
            g_thread_msgbox['state'] = state
            ctrl_fps = g_thread_msgbox['ctrl_fps']

        # Print some stats once per second:
        if timestamp_current - last_fps_update >= 1.0 or not g_sim_running:
            print("SIM=%4i MPC=%3i score=%i" % (sim_step_counter,
                                                ctrl_fps, reward_sum), end=' ')
            last_fps_update = timestamp_current
            sim_step_counter = 0
            env.print_state()
            #print("Thrust: %3.0f%% Thrust Vector alpha: %4.0f%% beta: %4.0f%% ATT_X: %3.0f%% ATT_Y: %3.0f%%" % (u[0]*100.0, u[1]*100.0, u[2]*100.0, u[3]*100.0, u[4]*100.0))

        # Terminate episode?
        if done is True:
            g_sim_running = False
            print("Total reward of episode: %i" % reward_sum)

    # Main Loop Finished. Wait for control thread to finish.
    nmpc_thread.join()

if __name__ == '__main__':
    main()
