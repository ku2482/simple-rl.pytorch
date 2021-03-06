import torch
from torch import nn
from torch.optim import Adam
import torch.nn.functional as F

from .sac import SAC
from simple_rl.network import TwinnedErrorFunc
from simple_rl.utils import soft_update, disable_gradient


class DisCor(SAC):

    def __init__(self, state_shape, action_shape, device, seed, batch_size=256,
                 gamma=0.99, nstep=1, replay_size=10**6, start_steps=10**4,
                 lr_actor=3e-4, lr_critic=3e-4, lr_alpha=3e-4, alpha_init=1.0,
                 target_update_coef=5e-3, lr_error=3e-4, tau_init=10.0,
                 start_steps_is=10**4):
        super().__init__(
            state_shape, action_shape, device, seed, batch_size, gamma, nstep,
            replay_size, start_steps, lr_actor, lr_critic, lr_alpha,
            alpha_init, target_update_coef)
        assert nstep == 1, 'DisCor only supports nstep=1.'

        self.error = TwinnedErrorFunc(
            state_shape=self.state_shape,
            action_shape=self.action_shape,
            hidden_units=[256, 256, 256],
            hidden_activation=nn.ReLU(inplace=True)
        ).to(self.device)
        self.error_target = TwinnedErrorFunc(
            state_shape=self.state_shape,
            action_shape=self.action_shape,
            hidden_units=[256, 256, 256],
            hidden_activation=nn.ReLU(inplace=True)
        ).to(self.device).eval()

        soft_update(self.error_target, self.error, 1.0)
        disable_gradient(self.error_target)

        self.optim_error = Adam(self.error.parameters(), lr=lr_error)
        self.tau1 = torch.tensor(tau_init, device=device, requires_grad=False)
        self.tau2 = torch.tensor(tau_init, device=device, requires_grad=False)

        self.start_steps_is = start_steps_is

    def update(self):
        self.learning_steps += 1
        states, actions, rewards, dones, next_states = \
            self.buffer.sample(self.batch_size)

        # Update critic networks with importance sampling.
        td_errors1, td_errors2 = self.update_critic_is(
            states, actions, rewards, dones, next_states
        )
        # Update error networks.
        self.update_error(
            states, actions, dones, next_states, td_errors1, td_errors2
        )
        # Update actor networks.
        self.update_actor(states)
        # Update target networks.
        self.update_target()

    def sample_next_errors(self, next_states):
        # Calculate next errors using sample approximation over policy.
        with torch.no_grad():
            next_actions, _ = self.actor.sample(next_states)
            return self.error_target(next_states, next_actions)

    def calculate_imp_ws(self, next_states, dones):
        next_errors1, next_errors2 = self.sample_next_errors(next_states)

        # Terms inside the exponent of Eq(8) of the paper.
        x1 = -(1.0 - dones) * self.gamma * next_errors1 / self.tau1
        x2 = -(1.0 - dones) * self.gamma * next_errors2 / self.tau2

        # Calculate self-normalized importance weights.
        return F.softmax(x1, dim=0), F.softmax(x2, dim=0)

    def update_critic_is(self, states, actions, rewards, dones, next_states):
        td_errors1, td_errors2 = self.calculate_td_error(
            states, actions, rewards, dones, next_states
        )

        # Don't use importance sampling during first some steps.
        if self.learning_steps >= self.start_steps_is:
            imp_ws1, imp_ws2 = self.calculate_imp_ws(next_states, dones)
        else:
            imp_ws1 = imp_ws2 = 1 / dones.size(0)

        # Critic's loss is the importance-weighted mean squared error.
        loss_critic1 = td_errors1.pow(2).mul_(imp_ws1).sum()
        loss_critic2 = td_errors2.pow(2).mul_(imp_ws2).sum()

        self.optim_critic.zero_grad()
        (loss_critic1 + loss_critic2).backward(retain_graph=False)
        self.optim_critic.step()

        return td_errors1.detach_(), td_errors2.detach_()

    def update_error(self, states, actions, dones, next_states, td_errors1,
                     td_errors2):
        curr_errors1, curr_errors2 = self.error(states, actions)
        next_errors1, next_errors2 = self.sample_next_errors(next_states)

        # These operations don't include gradient's calculations.
        target_errors1 = td_errors1 + self.gamma * (1.0 - dones) * next_errors1
        target_errors2 = td_errors2 + self.gamma * (1.0 - dones) * next_errors2

        loss_error1 = (curr_errors1 - target_errors1).pow_(2).mean()
        loss_error2 = (curr_errors2 - target_errors2).pow_(2).mean()

        self.optim_error.zero_grad()
        (loss_error1 + loss_error2).backward(retain_graph=False)
        self.optim_error.step()

        # Update taus using Polyak-Ruppert Averaging.
        # (i.e.) tau = (1 - 5e-3) * tau + 5e-3 * batch_mean(curr_error).
        with torch.no_grad():
            self.tau1.data.mul_(1.0 - self.target_update_coef)
            self.tau2.data.mul_(1.0 - self.target_update_coef)
            self.tau1.data.add_(
                self.target_update_coef * curr_errors1.mean().data)
            self.tau2.data.add_(
                self.target_update_coef * curr_errors2.mean().data)

    def update_target(self):
        super().update_target()
        soft_update(
            self.error_target,
            self.error,
            self.target_update_coef
        )
