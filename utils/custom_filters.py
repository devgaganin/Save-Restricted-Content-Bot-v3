# Copyright (c) 2025 devgagan : https://github.com/devgaganin.  
# Licensed under the GNU General Public License v3.0.  
# See LICENSE file in the repository root for full license text.

from pyrogram import filters

user_steps = {}

def login_filter_func(_, __, message):
    if not message.from_user:
        return False
    user_id = message.from_user.id
    return user_id in user_steps

login_in_progress = filters.create(login_filter_func)

def set_user_step(user_id, step=None):
    if step:
        user_steps[user_id] = step
    else:
        user_steps.pop(user_id, None)


def get_user_step(user_id):
    return user_steps.get(user_id)
