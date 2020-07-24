import numpy as np
import torch


class Buffer:

    def __init__(self, buffer_size, state_shape, action_shape, device,
                 save_log_pi=False):
        self._p = 0
        self._n = 0
        self.buffer_size = buffer_size
        self.save_log_pi = save_log_pi

        self.states = torch.empty(
            (buffer_size + 1, *state_shape), dtype=torch.float, device=device)
        self.actions = torch.empty(
            (buffer_size, *action_shape), dtype=torch.float, device=device)
        self.rewards = torch.empty(
            (buffer_size, 1), dtype=torch.float, device=device)
        self.dones = torch.empty(
            (buffer_size + 1, 1), dtype=torch.float, device=device)

        if save_log_pi:
            self.log_pis = torch.empty(
                (buffer_size, 1), dtype=torch.float, device=device)

    def reset(self, state):
        self.states[-1].copy_(torch.from_numpy(state))
        self.dones[-1] = 0

    def append(self, next_state, action, reward, done, log_pi=None):
        if self._p == 0:
            self.states[0].copy_(self.states[-1])
            self.dones[0].copy_(self.dones[-1])

        self.states[self._p + 1].copy_(torch.from_numpy(next_state))
        self.actions[self._p].copy_(torch.from_numpy(action))
        self.rewards[self._p] = float(reward)
        self.dones[self._p + 1] = float(done)

        if self.save_log_pi:
            self.log_pis[self._p] = float(log_pi)

        self._p = (self._p + 1) % self.buffer_size
        self._n = min(self._n + 1, self.buffer_size)

    def sample(self, batch_size):
        idxes = np.random.randint(low=0, high=self._n, size=batch_size)

        return (
            self.states[idxes],
            self.actions[idxes],
            self.rewards[idxes],
            self.states[idxes + 1],
            self.dones[idxes + 1]
        )